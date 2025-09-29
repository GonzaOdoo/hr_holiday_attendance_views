# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging
import base64
from odoo.exceptions import UserError
_logger = logging.getLogger(__name__)

class HrPayrollReport(models.Model):
    _name = 'hr.payroll.report.wizard'

    lote = fields.Many2one('hr.payslip.run')
    slips = fields.Many2many(
        'hr.payslip',
        string='Recibos de Nómina',
        domain="[('id', 'in', available_slip_ids)]"
    )
    available_slip_ids = fields.Many2many(
        'hr.payslip',
        compute='_compute_available_slips'
    )
    report_type = fields.Selection([
        ('salary', 'Liquidación de Salario'),
        ('bonus', 'Aguinaldo'),
        ('vacation', 'Vacaciones'),
    ], string='Tipo de Reporte',default='salary', required=True)

    @api.onchange('lote')
    def _onchange_lote(self):
        if self.lote:
            self.slips = self.lote.slip_ids
        else:
            self.slips = [(5, 0, 0)]  # Elimina todos

    @api.depends('lote')
    def _compute_available_slips(self):
        for wizard in self:
            wizard.available_slip_ids = wizard.lote.slip_ids

    def generate_ips_text(self):
        """
        Genera un archivo .txt en formato IPS con campos de ancho fijo.
        Procesa todos los payslips seleccionados en la vista lista.
        """
        # Usamos active_ids para asegurar que se procesen todos los seleccionados
        payslips = self.slips
    
        if not payslips:
            # Fallback: si no hay active_ids, usar self (caso del formulario individual)
            payslips = self
    
        if not payslips:
            raise UserError("No hay nóminas para generar el archivo.")
        company = payslips[0].company_id if payslips else self.env.company
        # Validar que los campos existan en la compañía
        if not company.ips:
            raise UserError("La compañía no tiene configurado el campo 'Número Patronal IPS'.")
        if not company.mtess:
            raise UserError("La compañía no tiene configurado el campo 'Número MTSS'.")
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
            numero_patronal = company.ips  # ← Reemplaza con valor real desde compañía
            numero_asegurado = company.mtess  # ← Puede venir del contrato o empleado
    
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

    def action_generate_txt_banco(self):
        # Usamos active_ids para asegurar que se procesen todos los seleccionados
        payslips = self.slips
        if not payslips:
            raise UserError("No hay nóminas para generar el archivo.")
    
        file_name = "txt_banco.txt"
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
    
            # Formateo de campos con ancho fijo
            ci = str(employee.identification_id or "").strip()
            debito =  str(employee.bank_account_id.acc_number or "")
            concepto = "15"
            salario_imponible = f"{imponible:010.2f}".replace('.', '') # Ej: 000150000 → 1500.00
            aguinaldo = f"NO"  # MMYYYY
            fecha_pago = record.paid_date
            dias_semana = {
                0: "lunes",
                1: "martes",
                2: "miércoles",
                3: "jueves",
                4: "viernes",
                5: "sábado",
                6: "domingo"
            }
            nombre_dia = dias_semana[fecha_pago.weekday()]
            fecha_formateada = f"{fecha_pago.day:02d}/{fecha_pago.month:02d}/{nombre_dia}"
            # Construir línea
            line = (
                '"' + ci + '"' + "," +
                '"' + debito + '"' + "," +
                '"' + concepto + '"' + "," +
                '"' + salario_imponible + '"' + "," +
                '"' + aguinaldo + '"' + "," +
                '"' + fecha_formateada + '"'
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
    def action_generate_report(self):
        self.ensure_one()
        if self.report_type == 'bonus':
            report_ref = 'hr_holiday_attendance_views.action_report_payslip_two_per_page_bonus'
        elif self.report_type == 'vacation':
            report_ref = 'hr_holiday_attendance_views.action_report_payslip_two_per_page_holiday'
        else:
            report_ref = 'hr_holiday_attendance_views.action_report_payslip_two_per_page'
        return self.env.ref(report_ref).report_action(self.slips)
    def action_generate_report2(self):
        self.ensure_one()
        if not self.slips:
            raise UserError("No hay recibos seleccionados para imprimir.")
    
        # Pasamos los slips al reporte
        #return self.env.ref('__export__.ir_act_report_xml_1086_d40b2ab8').report_action(self.slips)
        return self.env.ref('hr_holiday_attendance_views.action_report_payslip_two_per_page').report_action(self.slips)


    def action_generate_attendance(self):
        if not self.slips:
            raise UserError("No hay recibos seleccionados para imprimir.")
        return self.env.ref('hr_holiday_attendance_views.action_report_attendance').report_action(self.slips)
