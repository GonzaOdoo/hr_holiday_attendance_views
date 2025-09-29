# -*- coding: utf-8 -*-
from odoo import models, fields, api, Command
from datetime import date,datetime
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError
import io
import xlsxwriter
import base64
import logging
_logger = logging.getLogger(__name__)

class HrContract(models.Model):
    _inherit = 'hr.payslip'
    
    date_from_events = fields.Date(
        string='Inicio Novedades',
        compute='_compute_date_events',
        store=True,
        readonly=False,
    )
    date_to_events = fields.Date(
        string='Fin Novedades',
        compute='_compute_date_events',
        store=True,
        readonly=False,
    )

    @api.depends('date_to')  # Solo depende de date_to, porque es nuestra referencia
    def _compute_date_events(self):
        for payslip in self:
            if payslip.date_to:
                # Fin de novedades: siempre el día 20 del mes de date_to
                date_to_events = payslip.date_to.replace(day=20)

                # Inicio de novedades: 21 del mes anterior
                date_from_events = date_to_events - relativedelta(months=1)
                date_from_events = date_from_events.replace(day=21)

                payslip.date_from_events = date_from_events
                payslip.date_to_events = date_to_events
            else:
                payslip.date_from_events = False
                payslip.date_to_events = False

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
                day_rounded = 0
            attendance_line = {
                'sequence': work_entry_type.sequence,
                'work_entry_type_id': work_entry_type_id,
                'number_of_days': day_rounded,
                'number_of_hours': hours,
            }
            _logger.info(attendance_line)
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
        """Formatea un número como NN.NNN.NNN (SIN decimales, siempre)"""
        if amount is None:
            return "0"
        # Redondear al entero más cercano (o puedes usar int(amount) para truncar)
        amount = round(float(amount))  # Usa int(amount) si prefieres truncar en vez de redondear
        # Formatear con separadores de miles
        return "{:,}".format(int(amount)).replace(",", ".")

    def format_amount2(self, amount):
        """Formatea un número como NN.NNN.NNN (SIN decimales, siempre)"""
        if amount is None:
            return "0"
        # Redondear al entero más cercano (o puedes usar int(amount) para truncar)
        amount = round(float(amount))  # Usa int(amount) si prefieres truncar en vez de redondear
        # Formatear con separadores de miles
        return "{:,}".format(int(amount)).replace(",", ".")
    
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


    def generate_payslip_pivot_excel_report(self):
        # Soporta múltiples recibos
        if not self:
            return
    
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Nómina por Concepto')
    
        # Formatos
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'bg_color': '#16365C',
            'font_color': 'white',
            'border': 1,
        })
        text_format = workbook.add_format({'border': 1, 'align': 'left'})
        amount_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1, 'align': 'right'})
        date_format = workbook.add_format({'num_format': 'dd/mm/yyyy', 'border': 1})
        title_format = workbook.add_format({
            'bold': True,
            'font_size': 14,
            'align': 'left',
        })
    
        # Título
        current_row = 0
        worksheet.write(current_row, 0, 'Reporte de Nómina - Formato Pivotado', title_format)
        current_row += 2
    
        # === Mapeo personalizado: código de entrada → código de línea salarial ===
        CUSTOM_MAPPING = {
            'LATE': 'LATE',
            'WORK100': 'BASIC',
            'GUARD_DIURNA': 'GUARD_ASIG',
            'GUARD_NOCHE': 'GUARD_ASIG_NOCHE',
            # Agrega aquí más mapeos según necesites
        }
    
        # === Paso 1: Recolectar todos los conceptos únicos ===
        concept_info = {}  # {code: {name, sequence, type, mapped, salary_code, handled_by_mapping}}
    
        # 1. worked_days_line_ids
        all_worked_days = self.mapped('worked_days_line_ids')
        for wd in all_worked_days:
            if wd.code and wd.code not in concept_info:
                concept_info[wd.code] = {
                    'name': wd.work_entry_type_id.name,
                    'sequence': (wd.sequence or 100) - 1000,
                    'type': 'worked_days',
                    'mapped': False,
                    'salary_code': None,
                    'handled_by_mapping': False
                }
    
        # 2. input_line_ids
        all_inputs = self.mapped('input_line_ids')
        for inp in all_inputs:
            if inp.code and inp.code not in concept_info:
                concept_info[inp.code] = {
                    'name': inp.name,
                    'sequence': (inp.sequence or 500) - 500,
                    'type': 'input',
                    'mapped': False,
                    'salary_code': None,
                    'handled_by_mapping': False
                }
    
        # 3. line_ids — procesar mapeos y marcar como manejados
        all_salary_lines = self.mapped('line_ids')
        reverse_mapping = {v: k for k, v in CUSTOM_MAPPING.items()}  # salary_code → input_code
    
        for line in all_salary_lines:
            if not line.code:
                continue
    
            # Si este código de salario está mapeado desde un concepto de entrada
            if line.code in reverse_mapping:
                input_code = reverse_mapping[line.code]
    
                # ¡SIEMPRE marcar este código de salario como manejado por mapeo!
                if line.code not in concept_info:
                    concept_info[line.code] = {
                        'name': line.name,  # ¡Usamos el nombre ORIGINAL de la línea, no el del concepto!
                        'sequence': line.sequence if line.sequence is not None else 99999,
                        'type': 'salary',
                        'mapped': True,
                        'salary_code': None,
                        'handled_by_mapping': True
                    }
                else:
                    concept_info[line.code]['mapped'] = True
                    concept_info[line.code]['handled_by_mapping'] = True
                    concept_info[line.code]['name'] = line.name  # Aseguramos nombre original
    
                # Si el concepto de entrada existe, lo marcamos como mapeado
                if input_code in concept_info:
                    concept_info[input_code]['mapped'] = True
                    concept_info[input_code]['salary_code'] = line.code
    
            # Si coincide directamente con un concepto de entrada
            if line.code in concept_info and concept_info[line.code]['type'] in ['worked_days', 'input']:
                concept_info[line.code]['mapped'] = True
    
            # Agregar como línea independiente solo si NO está manejada y NO existe ya
            is_handled = concept_info.get(line.code, {}).get('handled_by_mapping', False)
            is_already_added = line.code in concept_info and concept_info[line.code]['type'] == 'salary'
    
            if not is_handled and not is_already_added:
                concept_info[line.code] = {
                    'name': line.name,
                    'sequence': line.sequence if line.sequence is not None else 99999,
                    'type': 'salary',
                    'mapped': False,
                    'salary_code': None,
                    'handled_by_mapping': False
                }
    
        # === Paso 2: Generar lista de códigos para columnas (excluir líneas de salario manejadas) ===
        sorted_codes = sorted(
            [
                code for code in concept_info.keys()
                if not (concept_info[code]['type'] == 'salary' and concept_info[code].get('handled_by_mapping', False))
            ],
            key=lambda code: concept_info[code]['sequence']
        )
    
        # Generar encabezados dinámicos
        dynamic_headers = []
        code_to_col_info = {}
    
        col_index = 0
        for code in sorted_codes:
            info = concept_info[code]
            base_name = info['name']
    
            if info['type'] in ['worked_days', 'input']:
                dynamic_headers.append(f"{base_name} (Valor)")
                dynamic_headers.append(f"{base_name} (Monto)")
                code_to_col_info[code] = {
                    'value_col': col_index,
                    'amount_col': col_index + 1,
                    'is_input': True
                }
                col_index += 2
            else:
                dynamic_headers.append(base_name)
                code_to_col_info[code] = {
                    'amount_col': col_index,
                    'is_input': False
                }
                col_index += 1
    
        # === Paso 3: Escribir cabeceras ===
        fixed_headers = ['Empleado', 'N° Recibo', 'Fecha Desde', 'Fecha Hasta']
        final_headers = fixed_headers + dynamic_headers + ['Total Neto', 'Estado']
    
        for col, header in enumerate(final_headers):
            worksheet.write(current_row, col, header, header_format)
    
        current_row += 1
    
        # === Paso 4: Escribir datos de cada recibo ===
        for slip in self:
            row_data = [
                slip.employee_id.name or '',
                slip.number or '',
                slip.date_from,
                slip.date_to,
            ]
    
            # Diccionarios por código
            wd_dict = {wd.code: wd.number_of_days or wd.number_of_hours for wd in slip.worked_days_line_ids}
            input_dict = {inp.code: inp.amount for inp in slip.input_line_ids}
            salary_dict = {line.code: line.amount for line in slip.line_ids}
    
            # Llenar columnas
            for code in sorted_codes:
                info = concept_info[code]
                if info['type'] in ['worked_days', 'input']:
                    # Valor
                    value = wd_dict.get(code, 0.0) if info['type'] == 'worked_days' else input_dict.get(code, 0.0)
                    row_data.append(value)
                    # Monto (usando mapeo si existe)
                    mapped_code = CUSTOM_MAPPING.get(code, code)
                    amount = salary_dict.get(mapped_code, 0.0)
                    row_data.append(amount)
                else:
                    # Línea de salario independiente
                    amount = salary_dict.get(code, 0.0)
                    row_data.append(amount)
    
            # Total neto y estado
            net_line = slip.line_ids.filtered(lambda l: l.code == 'NET')
            net_amount = net_line[0].amount if net_line else 0.0
            row_data.append(net_amount)
            row_data.append(dict(slip._fields['state'].selection).get(slip.state, slip.state))
    
            # Escribir fila
            for col, value in enumerate(row_data):
                if isinstance(value, (date, datetime)):
                    worksheet.write(current_row, col, value, date_format)
                elif isinstance(value, (float, int)):
                    worksheet.write(current_row, col, value, amount_format)
                else:
                    worksheet.write(current_row, col, str(value) if value != "" else "", text_format)
    
            current_row += 1
    
        # Ajustar anchos
        worksheet.set_column('A:A', 25)   # Empleado
        worksheet.set_column('B:B', 15)   # N° Recibo
        worksheet.set_column('C:D', 12)   # Fechas
        worksheet.set_column('E:ZZ', 18)  # Conceptos
    
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())
        output.close()
    
        # Nombre del archivo
        if len(self) == 1:
            date_str = self.date_from.strftime('%Y%m%d') if self.date_from else 'sin_fecha'
            filename = f"Recibo_Pivot_{self.number or 'SIN_NUMERO'}_{date_str}.xlsx"
        else:
            date_str = fields.Date.today().strftime('%Y%m%d')
            filename = f"Nomina_Pivot_{len(self)}_registros_{date_str}.xlsx"
    
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': self._name,
            'res_id': self.id if len(self) == 1 else False,
            'public': False,
        })
    
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }