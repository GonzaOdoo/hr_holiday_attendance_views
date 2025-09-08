# -*- coding: utf-8 -*-
from odoo import models, fields, api, Command
from odoo.exceptions import UserError
import base64
import logging
_logger = logging.getLogger(__name__)

class HrContract(models.Model):
    _inherit = 'hr.payslip'
    
    date_from_events = fields.Date('Inicio Novedades')
    date_to_events = fields.Date('Fin Novedades')

    @api.depends('employee_id', 'contract_id', 'struct_id', 'date_from', 'date_to', 'struct_id','date_from_events','date_to_events')
    def _compute_input_line_ids(self):
        attachment_type_ids = self.env['hr.payslip.input.type'].search([('available_in_attachments', '=', True)]).ids
        for slip in self:
            if not slip.employee_id or not slip.employee_id.salary_attachment_ids or not slip.struct_id:
                lines_to_remove = slip.input_line_ids.filtered(lambda x: x.input_type_id.id in attachment_type_ids)
                slip.update({'input_line_ids': [Command.unlink(line.id) for line in lines_to_remove]})
            if slip.employee_id.salary_attachment_ids and slip.date_to:
                lines_to_remove = slip.input_line_ids.filtered(lambda x: x.input_type_id.id in attachment_type_ids)
                input_line_vals = [Command.unlink(line.id) for line in lines_to_remove]

                if not slip.date_from_events:
                    slip.date_from_events = slip.date_from
                if not slip.date_to_events:
                    slip.date_to_events = slip.date_to
                valid_attachments = slip.employee_id.salary_attachment_ids.filtered(
                    lambda a: a.state == 'open'
                        and a.date_start <= slip.date_to_events
                        and (not a.date_end or a.date_end >= slip.date_from_events)
                        and (not a.other_input_type_id.struct_ids or slip.struct_id in a.other_input_type_id.struct_ids)
                )
                # Only take deduction types present in structure
                for input_type_id, attachments in valid_attachments.grouped("other_input_type_id").items():
                    amount = attachments._get_active_amount()
                    name = ', '.join(attachments.mapped('description'))
                    input_line_vals.append(Command.create({
                        'name': name,
                        'amount': amount if not slip.credit_note else -amount,
                        'input_type_id': input_type_id.id,
                    }))
                slip.update({'input_line_ids': input_line_vals})
    
    def _get_worked_day_lines_values(self, domain=None):
        self.ensure_one()
        res = []
        hours_per_day = self._get_worked_day_lines_hours_per_day()
        work_hours = self.contract_id.get_work_hours(self.date_from, self.date_to, domain=domain)
        work_hours_ordered = sorted(work_hours.items(), key=lambda x: x[1])
        biggest_work = work_hours_ordered[-1][0] if work_hours_ordered else 0
        add_days_rounding = 0
        leave_days = 0
        for work_entry_type_id, hours in work_hours.items():
            work_entry_type = self.env['hr.work.entry.type'].browse(work_entry_type_id)
            if work_entry_type.is_leave:
                days = round(hours / hours_per_day, 5) if hours_per_day else 0
                day_rounded = self._round_days(work_entry_type, days)
                leave_days += day_rounded  # Sumar los días de ausencia
        
        for work_entry_type_id, hours in work_hours_ordered:
            work_entry_type = self.env['hr.work.entry.type'].browse(work_entry_type_id)
            days = round(hours / hours_per_day, 5) if hours_per_day else 0
            if work_entry_type_id == biggest_work:
                days += add_days_rounding
            day_rounded = self._round_days(work_entry_type, days)
            add_days_rounding += (days - day_rounded)
            if work_entry_type.code == 'WORK100':
                day_rounded = max(0, 30 - round(leave_days, 5))
            if work_entry_type.code in ['OVERTIME_EVENING','OVERTIME_NIGHT','OVERTIME']:
                _logger.info("Overtime!")
                days_rounded = 0
            attendance_line = {
                'sequence': work_entry_type.sequence,
                'work_entry_type_id': work_entry_type_id,
                'number_of_days': day_rounded,
                'number_of_hours': hours,
            }
            res.append(attendance_line)

        # Sort by Work Entry Type sequence
        work_entry_type = self.env['hr.work.entry.type']
        return sorted(res, key=lambda d: work_entry_type.browse(d['work_entry_type_id']).sequence)
    
    def _get_default_month(self):
        return fields.Date.context_today(self).strftime('%m')

    month_period = fields.Selection([
        ('01', 'Enero'),
        ('02', 'Febrero'),
        ('03', 'Marzo'),
        ('04', 'Abril'),
        ('05', 'Mayo'),
        ('06', 'Junio'),
        ('07', 'Julio'),
        ('08', 'Agosto'),
        ('09', 'Septiembre'),
        ('10', 'Octubre'),
        ('11', 'Noviembre'),
        ('12', 'Diciembre')
    ], string='Mes del período', default=_get_default_month)

    def format_amount(self, amount):
        """Formatea un número como NN.NNN.NNN,DD"""
        if amount is None:
            return "0,00"
        # Redondear a 2 decimales
        amount = round(float(amount), 2)
        # Convertir a string y separar parte entera y decimal
        integer_part, decimal_part = f"{amount:.2f}".split('.')
        # Formatear miles con puntos
        integer_part = "{:,}".format(int(integer_part)).replace(",", ".")
        # Unir con coma
        return f"{integer_part},{decimal_part}"


    def generate_ips_text(self):
        """
        Genera un archivo .txt en formato IPS con campos de ancho fijo.
        Procesa todos los payslips seleccionados en la vista lista.
        """
        # Usamos active_ids para asegurar que se procesen todos los seleccionados
        payslips = self.env['hr.payslip'].browse(self._context.get('active_ids', []))
    
        if not payslips:
            # Fallback: si no hay active_ids, usar self (caso del formulario individual)
            payslips = self
    
        if not payslips:
            raise UserError("No hay nóminas para generar el archivo.")
    
        file_name = "reporte_ips.txt"
        lines = []
    
        for record in payslips:
            employee = record.employee_id
            contract = record.contract_id
    
            # Obtener salario imponible (GROSS) y neto (NET)
            imponible = 0
            real = 0
            for line in record.line_ids:
                if line.code == 'GROSS':
                    imponible = line.amount
                elif line.code == 'NET':
                    real = line.amount
    
            # Validaciones básicas
            if not employee.identification_id:
                raise UserError(f"Empleado {employee.name} no tiene número de cédula.")
    
            # === Campos del formato IPS ===
            numero_patronal = "1234567890"  # ← Reemplaza con valor real desde compañía
            numero_asegurado = "0000000000"  # ← Puede venir del contrato o empleado
    
            # Formateo de campos con ancho fijo
            cedula = str(employee.identification_id or "").strip()[:10].ljust(10)
            apellidos = str(employee.legal_name or employee.legal_name.split()[-1] if employee.legal_name else "").strip()[:30].ljust(30)
            nombres = str(employee.legal_last_name or " ".join(employee.legal_last_name.split()[:-1]) if employee.legal_last_name else "").strip()[:30].ljust(30)
            categoria = "E".ljust(1)  # E = Empleado activo
            dias_trabajados = "30".zfill(2)  # Puedes calcularlo si tienes datos
            salario_imponible = f"{imponible:010.2f}".replace('.', '')[:10].zfill(10)  # Ej: 000150000 → 1500.00
            mes_y_anio = f"{record.date_to.month:02d}{record.date_to.year}"  # MMYYYY
            codigo_movimiento = "01".ljust(2)
            salario_real = f"{real:010.2f}".replace('.', '')[:10].zfill(10)
    
            # Construir línea
            line = (
                numero_patronal.ljust(10) +
                numero_asegurado.ljust(10) +
                cedula +
                apellidos +
                nombres +
                categoria +
                dias_trabajados +
                salario_imponible +
                mes_y_anio.ljust(6) +
                codigo_movimiento +
                salario_real
            )
    
            lines.append(line)
    
        # Generar contenido
        file_content = '\n'.join(lines)
    
        # Codificar
        file_data = base64.b64encode(file_content.encode('utf-8')).decode('utf-8')
    
        # Crear adjunto
        attachment = self.env['ir.attachment'].create({
            'name': file_name,
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'text/plain',
            'res_model': 'hr.payslip',
            'res_id': payslips[0].id,  # Referencia al primer registro
        })
    
        # Devolver acción de descarga
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }