# -*- coding: utf-8 -*-
from odoo import models, fields, api
from datetime import date,datetime,timedelta
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError
from calendar import monthrange
import io
import xlsxwriter
import base64
import logging
_logger = logging.getLogger(__name__)

class LaborReportWizard(models.TransientModel):
    _name = 'labor.report.wizard'
    _description = 'Asistente para generar libros laborales'

    report_type = fields.Selection([
        ('book1', 'Planilla Anual Empleados y Obreros'),
        ('book2', 'Planilla Anual Sueldos y Jornales'),
        ('book3', 'Planilla Resumen Gral. De Personas ocupadas'),
        # Agrega más opciones según tus necesidades
    ], string="Tipo de Reporte", required=True)
    year = fields.Integer(string="Año", required=True, default=lambda self: fields.Date.today().year)
    company_id = fields.Many2one('res.company', string="Compañía", default=lambda self: self.env.company)
    
    def action_generate_report(self):
        """Método que se llamará al hacer clic en el botón Generar"""
        self.ensure_one()
        # Aquí puedes llamar a tu lógica de generación de reportes
        # Por ejemplo, devolver una acción para descargar un PDF o Excel
        # Por ahora, dejamos un placeholder
        if self.report_type == 'book1':
            return self.generate_listado_empleados_excel()
        elif self.report_type == 'book2':
            return self.generate_planilla_anual_empleados_obreros()
        elif self.report_type == 'book3':
            return self.generate_resumen_personas_ocupadas_excel()

    def _generate_book1(self):
        # Lógica específica para el Libro de Asistencias
        # Por ejemplo, devolver un reporte QWeb o XLSX
        return 
    def _generate_book2(self):
        return 

    def _generate_book3(self):
        return

    def generate_planilla_anual_empleados_obreros(self):
        self.ensure_one()
        year = self.year
        company = self.company_id
    
        # Buscar todos los payslips del año y compañía
        date_from = date(year, 1, 1)
        date_to = date(year, 12, 31)
        payslips = self.env['hr.payslip'].search([
            ('date_from', '>=', date_from),
            ('date_to', '<=', date_to),
            ('company_id', '=', company.id),
            ('state', 'in', ['done', 'paid'])
        ])
    
        if not payslips:
            raise UserError("No se encontraron nóminas procesadas para el año %s en la compañía %s." % (year, company.name))
    
        # Agrupar por empleado
        employees_data = {}
        for slip in payslips:
            emp = slip.employee_id
            if emp.id not in employees_data:
                employees_data[emp.id] = {
                    'employee': emp,
                    'slips': [],
                    'months': {m: None for m in range(1, 13)}
                }
            employees_data[emp.id]['slips'].append(slip)
            month = slip.date_from.month
            employees_data[emp.id]['months'][month] = slip
    
        # Clasificar empleados
        categories = {
            'SUBJEFES MUJERES': [],
            'SUBJEFES VARONES': [],
            'EMPLEADOS MUJERES': [],
            'EMPLEADOS VARONES': [],
        }
    
        for emp_id, data in employees_data.items():
            emp = data['employee']
            is_subjefe = emp.is_boss
            gender = emp.gender or 'male'
            key = None
            if is_subjefe:
                key = 'SUBJEFES MUJERES' if gender == 'female' else 'SUBJEFES VARONES'
            else:
                key = 'EMPLEADOS MUJERES' if gender == 'female' else 'EMPLEADOS VARONES'
            categories[key].append(data)
    
        # === CALCULAR RESUMEN POR CATEGORÍA ===
        summary = {}
        total_general_cant = 0
        total_general_horas = 0
        total_general_imponible = 0
        total_general_total = 0
    
        for cat_key, emp_list in categories.items():
            cant = len(emp_list)
            total_h = total_s = total_general = 0
    
            for emp_data in emp_list:
                emp = emp_data['employee']
                months = emp_data['months']
    
                horas = [0] * 12
                sueldos = [0] * 12
                aguinaldo = bonificaciones = vacaciones = 0
                total_s_50 = total_s_100 = 0
    
                for m in range(1, 13):
                    slip = months[m]
                    if not slip:
                        continue
    
                    work_days = slip.worked_days_line_ids.filtered(lambda w: w.code == 'WORK100')
                    # Asumiendo 8h/día → total horas = días * 8
                    h_norm = sum(w.number_of_days for w in work_days) * 8
                    horas[m-1] = h_norm
    
                    s_norm = slip.line_ids.filtered(lambda l: l.code == 'BASIC').amount or 0
                    sueldos[m-1] = s_norm
    
                    total_s_50 += slip.line_ids.filtered(lambda l: l.code == 'HEX50').amount or 0
                    total_s_100 += slip.line_ids.filtered(lambda l: l.code == 'HNOC30').amount or 0
                    aguinaldo += slip.line_ids.filtered(lambda l: l.code == 'AGUINALDO').amount or 0
                    vacaciones += slip.line_ids.filtered(lambda l: l.code == 'VACACIONES').amount or 0
                    bonificaciones += slip.line_ids.filtered(lambda l: l.code == 'BONIF_FAMILIAR').amount or 0
    
                total_h += sum(horas)
                total_s += sum(sueldos)
                total_general += total_s + total_s_50 + total_s_100 + aguinaldo + vacaciones + bonificaciones
    
            summary[cat_key] = {
                'cantidad': cant,
                'horas': total_h,
                'imponible': total_s,  # o total_general si "imponible" incluye extras
                'total': total_general
            }
    
            total_general_cant += cant
            total_general_horas += total_h
            total_general_imponible += total_s
            total_general_total += total_general
    
        # === CREAR EXCEL ===
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
        bold = workbook.add_format({'bold': True})
        bold_center = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter'})
        normal = workbook.add_format({'border': 1})
    
        # === HOJA RESUMEN ===
        ws_resumen = workbook.add_worksheet('RESUMEN')
        ws_resumen.set_landscape()
        ws_resumen.set_paper(9)
    
        # Título
        ws_resumen.merge_range('A1:E1', 'RESUMEN', bold)
        ws_resumen.write_row(2, 0, ['TIPO DE EMPLEADOS', 'CANTIDAD', 'TOTAL DE HORAS', 'TOTAL IMPONIBLE', 'TOTAL GENERAL'], bold_center)
    
        row = 3
        for cat in ['SUBJEFES MUJERES', 'SUBJEFES VARONES', 'EMPLEADOS MUJERES', 'EMPLEADOS VARONES']:
            data = summary[cat]
            ws_resumen.write_row(row, 0, [
                cat,
                data['cantidad'],
                data['horas'],
                data['imponible'],
                data['total']
            ], normal)
            row += 1
    
        # Total general
        ws_resumen.write_row(row, 0, [
            'TOTAL GENERAL',
            total_general_cant,
            total_general_horas,
            total_general_imponible,
            total_general_total
        ], bold)
    
        # === ENCABEZADOS COMUNES PARA HOJAS DETALLADAS ===
        headers = [
            'NRO_PATRONAL', 'DOCUMENTO', 'FORMADEPAGO',
            'H_ENE', 'S_ENE', 'H_FEB', 'S_FEB', 'H_MAR', 'S_MAR', 'H_ABR', 'S_ABR',
            'H_MAY', 'S_MAY', 'H_JUN', 'S_JUN', 'H_JUL', 'S_JUL', 'H_AGO', 'S_AGO',
            'H_SET', 'S_SET', 'H_OCT', 'S_OCT', 'H_NOV', 'S_NOV', 'H_DIC', 'S_DIC',
            'H_50', 'S_50', 'H_100', 'S_100', 'AGUINALDO', 'BENEFICIOS', 'BONIFICACIONES', 'VACACIONES',
            'TOTAL_H', 'TOTAL_S', 'TOTALGENERAL'
        ]
    
        center = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
    
        def write_sheet(worksheet, emp_list):
            worksheet.set_landscape()
            worksheet.set_paper(9)
            row = 0
            for col, h in enumerate(headers):
                worksheet.write(row, col, h, bold_center)
            row += 1
    
            for emp_data in emp_list:
                emp = emp_data['employee']
                months = emp_data['months']
    
                horas = [0] * 12
                sueldos = [0] * 12
                total_h_50 = total_s_50 = 0
                total_h_100 = total_s_100 = 0
                aguinaldo = beneficios = bonificaciones = vacaciones = 0
    
                for m in range(1, 13):
                    slip = months[m]
                    if not slip:
                        continue
    
                    work_days = slip.worked_days_line_ids.filtered(lambda w: w.code == 'WORK100')
                    h_norm = sum(w.number_of_days for w in work_days) * 8  # ← Ajuste: días → horas
                    horas[m-1] = h_norm
    
                    s_norm = slip.line_ids.filtered(lambda l: l.code == 'BASIC').amount or 0
                    sueldos[m-1] = s_norm
    
                    he_50 = slip.worked_days_line_ids.filtered(lambda w: w.code == 'OVERTIME_EVENING')
                    total_h_50 += sum(w.number_of_hours for w in he_50)
                    total_s_50 += slip.line_ids.filtered(lambda l: l.code == 'HEX50').amount or 0
    
                    he_100 = slip.worked_days_line_ids.filtered(lambda w: w.code == 'OVERTIME_NIGHT')
                    total_h_100 += sum(w.number_of_hours for w in he_100)
                    total_s_100 += slip.line_ids.filtered(lambda l: l.code == 'HNOC30').amount or 0
    
                    aguinaldo += slip.line_ids.filtered(lambda l: l.code == 'AGUINALDO').amount or 0
                    vacaciones += slip.line_ids.filtered(lambda l: l.code == 'VACACIONES').amount or 0
                    bonificaciones += slip.line_ids.filtered(lambda l: l.code == 'BONIF_FAMILIAR').amount or 0
    
                total_h = sum(horas)
                total_s = sum(sueldos)
                total_general = total_s + total_s_50 + total_s_100 + aguinaldo + vacaciones + bonificaciones
    
                fila = [
                    company.ips or '',
                    emp.identification_id or '',
                    'M',
                    *sum([[horas[i], sueldos[i]] for i in range(12)], []),
                    total_h_50, total_s_50,
                    total_h_100, total_s_100,
                    aguinaldo, beneficios, bonificaciones, vacaciones,
                    total_h, total_s, total_general
                ]
    
                for col, val in enumerate(fila):
                    fmt = center if col < 3 else normal
                    worksheet.write(row, col, val or '', fmt)
                row += 1
    
        # Crear hojas por categoría
        for title, emp_list in categories.items():
            if emp_list:
                sheet_name = title[:31]
                worksheet = workbook.add_worksheet(sheet_name)
                write_sheet(worksheet, emp_list)
    
        # Cerrar y devolver
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())
        output.close()
    
        filename = f"Planilla_Anual_Sueldos_y_Jornales_{year}.xlsx"
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
    
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }


    def generate_resumen_personas_ocupadas_excel(self):
        self.ensure_one()
        year = self.year
        company = self.company_id
    
        # Rango de fechas del año
        date_from = date(year, 1, 1)
        date_to = date(year, 12, 31)
    
        # Buscar empleados que tuvieron contrato activo EN ALGÚN MOMENTO del año
        employees = self.env['hr.employee'].search([
            ('company_id', '=', company.id),
            '|',
            ('first_contract_date', '<=', date_to),
            ('departure_date', '>=', date_from),
            '|',
            ('departure_date', '=', False),
            ('departure_date', '>=', date_from)
        ])
    
        # Clasificar empleados
        categories = {
            'SUBJEFES VARONES': {'list': [], 'ingresos': 0, 'salidas': 0},
            'SUBJEFES MUJERES': {'list': [], 'ingresos': 0, 'salidas': 0},
            'EMPLEADOS VARONES': {'list': [], 'ingresos': 0, 'salidas': 0},
            'EMPLEADOS MUJERES': {'list': [], 'ingresos': 0, 'salidas': 0},
            # Obreros y Menores: dejamos vacíos (0)
        }
    
        for emp in employees:
            is_boss = emp.is_boss
            gender = emp.gender or 'male'
    
            # Determinar categoría
            if is_boss:
                cat_key = 'SUBJEFES MUJERES' if gender == 'female' else 'SUBJEFES VARONES'
            else:
                cat_key = 'EMPLEADOS MUJERES' if gender == 'female' else 'EMPLEADOS VARONES'
    
            if cat_key in categories:
                categories[cat_key]['list'].append(emp)
    
                # Contar ingresos (contrato iniciado en el año)
                if emp.first_contract_date and emp.first_contract_date.year == year:
                    categories[cat_key]['ingresos'] += 1
    
                # Contar salidas (baja en el año)
                if emp.departure_date and emp.departure_date.year == year:
                    categories[cat_key]['salidas'] += 1
    
        # Buscar payslips del año para calcular horas y montos
        payslips = self.env['hr.payslip'].search([
            ('date_from', '>=', date_from),
            ('date_to', '<=', date_to),
            ('company_id', '=', company.id),
            ('state', 'in', ['done', 'paid'])
        ])
    
        # Acumuladores por categoría
        summary_data = {
            'SUBJEFES VARONES': {'horas': 0, 'monto': 0},
            'SUBJEFES MUJERES': {'horas': 0, 'monto': 0},
            'EMPLEADOS VARONES': {'horas': 0, 'monto': 0},
            'EMPLEADOS MUJERES': {'horas': 0, 'monto': 0},
        }
    
        for slip in payslips:
            emp = slip.employee_id
            is_boss = emp.is_boss
            gender = emp.gender or 'male'
    
            if is_boss:
                cat_key = 'SUBJEFES MUJERES' if gender == 'female' else 'SUBJEFES VARONES'
            else:
                cat_key = 'EMPLEADOS MUJERES' if gender == 'female' else 'EMPLEADOS VARONES'
    
            if cat_key not in summary_data:
                continue
    
            # Horas normales (WORK100 → días → *8)
            work_days = slip.worked_days_line_ids.filtered(lambda w: w.code == 'WORK100')
            horas = sum(w.number_of_days for w in work_days) * 8
            summary_data[cat_key]['horas'] += horas
    
            # Monto total pagado (NET o BASIC + extras, según normativa)
            net = slip.line_ids.filtered(lambda l: l.code == 'NET').amount or 0
            summary_data[cat_key]['monto'] += net
    
        # === CREAR EXCEL ===
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Resumen Personas Ocupadas')
    
        bold = workbook.add_format({'bold': True})
        normal = workbook.add_format()
    
        # Encabezados
        headers = [
            'NRO_PATRONAL', 'ANHO',
            'SUPJEFESVARONES', 'SUPJEFESMUJERES',
            'EMPLEADOSVARONES', 'EMPLEADOSMUJERES',
            'OBREROSVARONES', 'OBREROSMUJERES',
            'MENORESVARONES', 'MENORESMUJERES',
            'ORDEN', ''
        ]
    
        for col, h in enumerate(headers):
            worksheet.write(0, col, h, bold)
        worksheet.set_column(0, 0, 30)
        worksheet.set_column(1, 1, 30)
        worksheet.set_column(2, 9, 30)
        worksheet.set_column(10, 10,30)
        worksheet.set_column(11, 11, 30)
        # Valores por orden
        nro_patronal = company.ips or ''
        anho = year
    
        # Mapeo de columnas
        col_map = {
            'SUBJEFES VARONES': 2,
            'SUBJEFES MUJERES': 3,
            'EMPLEADOS VARONES': 4,
            'EMPLEADOS MUJERES': 5,
        }
    
        for row_offset, (orden, desc) in enumerate([
            (1, "Cantidad de personas"),
            (2, "Cantidad horas trabajadas por esa persona"),
            (3, "Cantidad monetaria pagada a esa persona"),
            (4, "Cantidad de personas ingresadas en la misma categoria"),
            (5, "Cantidad de salidas de personas en esa categoria"),
        ], start=1):
    
            row = [nro_patronal, anho] + [''] * 8 + [orden, desc]
    
            if orden == 1:  # Cantidad de personas
                for cat, col_idx in col_map.items():
                    row[col_idx] = len(categories[cat]['list'])
            elif orden == 2:  # Horas
                for cat, col_idx in col_map.items():
                    row[col_idx] = summary_data[cat]['horas']
            elif orden == 3:  # Monto
                for cat, col_idx in col_map.items():
                    row[col_idx] = summary_data[cat]['monto']
            elif orden == 4:  # Ingresos
                for cat, col_idx in col_map.items():
                    row[col_idx] = categories[cat]['ingresos']
            elif orden == 5:  # Salidas
                for cat, col_idx in col_map.items():
                    row[col_idx] = categories[cat]['salidas']
    
            # Escribir fila
            for col, val in enumerate(row):
                worksheet.write(row_offset, col, val or '', normal)
        ws_listado = workbook.add_worksheet('LISTADO EMPLEADOS')
        ws_listado.set_landscape()
        ws_listado.set_paper(9)

        # Encabezados
        listado_headers = [
            'NROPATRONAL', 'DOCUMENTO', 'NOMBRE', 'APELLIDO', 'SEXO', 'ESTADOCIVIL',
            'FECHANAC', 'NACIONALIDAD', 'DOMICILIO'
        ]
        for col, h in enumerate(listado_headers):
            ws_listado.write(0, col, h, bold)

        row = 1
        for emp in employees:  # ya tienes esta variable desde antes
            # NOMBRE y APELLIDO: si usas hr_employee_firstname
            nombre = emp.legal_name or ''
            apellido = emp.legal_last_name or ''
            # Si NO usas ese módulo, puedes hacer:
            # parts = (emp.name or '').split(' ', 1)
            # nombre = parts[0]
            # apellido = parts[1] if len(parts) > 1 else ''

            sexo = 'M' if emp.gender == 'male' else 'F' if emp.gender == 'female' else ''
            estado_civil = self.marital_status(emp.marital if emp.marital else 'single')
            fecha_nac = emp.birthday.strftime('%Y-%m-%d') if emp.birthday else ''
            nacionalidad = emp.country_id.name or ''
            domicilio = emp.private_street or ''

            fila = [
                company.ips or '',
                emp.identification_id or '',
                nombre,
                apellido,
                sexo,
                estado_civil,
                fecha_nac,
                nacionalidad,
                domicilio
            ]

            for col, val in enumerate(fila):
                ws_listado.write(row, col, val or '', normal)
            ws_listado.set_column(0, 0, 20)  # NROPATRONAL
            ws_listado.set_column(1, 1, 20)  # DOCUMENTO
            ws_listado.set_column(2, 3, 25)  # NOMBRE, APELLIDO
            ws_listado.set_column(4, 4, 8)   # SEXO
            ws_listado.set_column(5, 5, 15)  # ESTADOCIVIL
            ws_listado.set_column(6, 6, 15)  # FECHANAC
            ws_listado.set_column(7, 7, 20)  # NACIONALIDAD
            ws_listado.set_column(8, 8, 30)  # DOMICILIO
            row += 1
        # Cerrar
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())
        output.close()
    
        filename = f"Resumen_Personas_Ocupadas_{year}.xlsx"
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
    
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def generate_listado_empleados_excel(self):
        self.ensure_one()
        company = self.company_id
    
        # Buscar todos los empleados de la compañía
        employees = self.env['hr.employee'].search([
            ('company_id', '=', company.id)
        ])
    
        if not employees:
            raise UserError("No se encontraron empleados en la compañía %s." % company.name)
    
        # Crear archivo en memoria
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Empleados')
    
        # Formatos
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'bg_color': '#2C3E50',
            'font_color': 'white',
            'border': 1,
            'text_wrap': True
        })
        text_format = workbook.add_format({'border': 1, 'align': 'left'})
    
        # Cabeceras
        headers = [
            'NROPATRONAL', 'DOCUMENTO', 'NOMBRE', 'APELLIDO', 'SEXO', 'ESTADOCIVIL',
            'FECHANAC', 'NACIONALIDAD', 'DOMICILIO', 'FECHANACMENOR', 'HIJOSMENORES',
            'CARGO', 'PROFESION', 'FECHAENTRADA', 'HORARIOTRABAJO', 'MENORESCAPA',
            'MENORESESCOLAR', 'FECHASALIDA', 'MOTIVOSALIDA', 'ESTADO'
        ]
    
        # Escribir cabeceras
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)
    
        # Escribir datos
        row_index = 1
        for emp in employees:
            nro_patronal = company.ips or '0'
            documento = emp.identification_id or ''
            nombre = emp.legal_name or emp.name or ''
            apellido = emp.legal_last_name or ''
            sexo = 'M' if emp.gender == 'male' else 'F' if emp.gender == 'female' else ''
            estado_civil = self.marital_status(emp.marital or 'single')
            fecha_nac = emp.birthday.strftime('%Y-%m-%d') if emp.birthday else ''
            nacionalidad = (emp.country_id.name or 'PARAGUAYA').upper()
            domicilio = emp.private_street or ''
            fecha_nac_menor = ''  # ajusta si tienes el campo
            hijos_menores = emp.children or 0
            cargo = emp.job_id.name or ''
            profesion = 'EMPLEADO'  # o usa un campo si lo tienes
            fecha_entrada = emp.first_contract_date.strftime('%Y-%m-%d') if emp.first_contract_date else ''
            horario_trabajo = emp.resource_calendar_id.name or ''
            menores_capa = ''  # ajusta si tienes el campo
            menores_escolar = ''  # ajusta si tienes el campo
            fecha_salida = emp.departure_date.strftime('%Y-%m-%d') if emp.departure_date else ''
            motivo_salida = emp.departure_description or ''
            estado = 'ACTIVO' if emp.active else 'INACTIVO'
    
            row_data = [
                nro_patronal, documento, nombre, apellido, sexo, estado_civil,
                fecha_nac, nacionalidad, domicilio, fecha_nac_menor, hijos_menores,
                cargo, profesion, fecha_entrada, horario_trabajo, menores_capa,
                menores_escolar, fecha_salida, motivo_salida, estado
            ]
    
            for col, value in enumerate(row_data):
                worksheet.write(row_index, col, value or '', text_format)
            row_index += 1
    
        # Ajustar ancho de columnas
        worksheet.set_column('A:T', 18)
    
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())
        output.close()
    
        filename = f"Listado_Empleados_{company.name}_{fields.Date.today().strftime('%Y%m%d')}.xlsx"
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
    
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }



    def marital_status(self,status):
        if status == 'married':
            return 'C'
        elif status == 'divorced':
            return 'D'
        elif status == 'widowwe':
            return 'V'
        return 'S'
