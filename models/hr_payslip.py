# -*- coding: utf-8 -*-
from odoo import models, fields, api, Command
from datetime import date,datetime,timedelta
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError
from calendar import monthrange
import io
import xlsxwriter
import base64
import pytz
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
    struct_type = fields.Selection('Tipo de estructura',related='struct_id.schedule_pay')

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
                
    @api.depends('contract_id', 'struct_id')
    def _compute_date_from(self):
        for payslip in self:
            if self.env.context.get('default_date_from'):
                payslip.date_from = self.env.context.get('default_date_from')
            else:
                # Si la estructura es anual, sugerir 1 de enero del año actual
                if payslip.struct_id and payslip.struct_id.schedule_pay == 'annually':
                    today = fields.Date.today()
                    payslip.date_from = today.replace(month=1, day=1)
                else:
                    payslip.date_from = payslip._get_schedule_period_start()

    def _get_schedule_timedelta(self):
        self.ensure_one()
        # Prioriza struct_id.schedule_pay; si no está, usa el del contrato o el tipo de estructura
        schedule = self.struct_id.schedule_pay or \
                   (self.contract_id.schedule_pay if self.contract_id else False) or \
                   (self.contract_id.structure_type_id.default_schedule_pay if self.contract_id and self.contract_id.structure_type_id else False)

        # Si aún no hay schedule, usa 'monthly' como fallback
        if not schedule:
            schedule = 'monthly'

        if schedule == 'quarterly':
            timedelta = relativedelta(months=3, days=-1)
        elif schedule == 'semi-annually':
            timedelta = relativedelta(months=6, days=-1)
        elif schedule == 'annually':
            timedelta = relativedelta(years=1, days=-1)
        elif schedule == 'weekly':
            timedelta = relativedelta(days=6)
        elif schedule == 'bi-weekly':
            timedelta = relativedelta(days=13)
        elif schedule == 'semi-monthly':
            # Para semi-monthly, el cálculo depende del día de date_from
            if not self.date_from:
                # Si no hay date_from, no podemos calcular; devolvemos 0 días
                timedelta = relativedelta(days=0)
            else:
                # Primera quincena: del 1 al 15; segunda: del 16 al último día del mes
                if self.date_from.day <= 15:
                    # Finaliza el 15
                    timedelta = relativedelta(day=15)
                else:
                    # Finaliza el último día del mes
                    timedelta = relativedelta(day=31)
        elif schedule == 'bi-monthly':
            timedelta = relativedelta(months=2, days=-1)
        elif schedule == 'daily':
            timedelta = relativedelta(days=0)
        else:  # monthly por defecto
            timedelta = relativedelta(months=1, days=-1)
        return timedelta

    @api.depends('date_from', 'contract_id', 'struct_id')
    def _compute_date_to(self):
        for payslip in self:
            if self.env.context.get('default_date_to'):
                payslip.date_to = self.env.context.get('default_date_to')
            else:
                if payslip.date_from:
                    payslip.date_to = payslip.date_from + payslip._get_schedule_timedelta()
                else:
                    payslip.date_to = False

            # Ajustar date_to si excede la fecha de fin del contrato
            if payslip.contract_id and payslip.contract_id.date_end and payslip.date_from \
                    and payslip.date_from >= payslip.contract_id.date_start \
                    and payslip.date_from < payslip.contract_id.date_end \
                    and payslip.date_to and payslip.date_to > payslip.contract_id.date_end:
                payslip.date_to = payslip.contract_id.date_end


    

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
                        and a.date_start >= slip.date_from_events
                        and (not a.date_end or a.date_end <= slip.date_to_events)
                        and (not a.other_input_type_id.struct_ids or slip.struct_id in a.other_input_type_id.struct_ids)
                )
                _logger.info(valid_attachments)
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

    def get_absences(self,work_hours):
        _logger.info("Ausencias")
        _logger.info(work_hours)
        return
    
    def _get_worked_day_lines_values(self, domain=None):
        self.ensure_one()
        res = []
        hours_per_day = self._get_worked_day_lines_hours_per_day()
        work_hours = self.contract_id.get_work_hours(self.date_from_events, self.date_to_events, domain=domain)
        work_hours_ordered = sorted(work_hours.items(), key=lambda x: x[1])
        _logger.info("Horas de trabajo!")
        _logger.info(work_hours_ordered)
        biggest_work = work_hours_ordered[-1][0] if work_hours_ordered else 0
        add_days_rounding = 0
        is_final_liquidation = self.struct_id.is_final_liquidation
        # === Paso 1: Calcular todas las ausencias (incluyendo no justificadas) ===
        leave_days = 0
        payslip_start = self.date_from
        payslip_end = self.date_to
        contract = self.contract_id
        
        # Asumimos siempre 30 días base
        base_days = 30.0
        days_outside_contract = 0.0
        
        if contract:
            contract_start = contract.date_start
            contract_end = contract.date_end or payslip_end  # si no termina, cubre hasta el fin del período
            
            # Días al inicio del período que están ANTES del inicio del contrato
            if payslip_start < contract_start:
                # Contar días desde payslip_start hasta contract_start - 1
                gap_start = payslip_start
                gap_end = min(contract_start - timedelta(days=1), payslip_end)
                if gap_end >= gap_start:
                    days_outside_contract += (gap_end - gap_start).days + 1
        
            # Días al final del período que están DESPUÉS del fin del contrato
            if contract_end < payslip_end:
                gap_start = max(contract_end + timedelta(days=1), payslip_start)
                gap_end = payslip_end
                if gap_end >= gap_start:
                    days_outside_contract += (gap_end - gap_start).days + 1
        _logger.info(days_outside_contract)
        covered_start = max(payslip_start, contract.date_start)
        covered_end = min(payslip_end, contract.date_end or payslip_end)
        
        if covered_end >= covered_start:
            days_covered = (covered_end - covered_start).days + 1
        else:
            days_covered = 0
        
        max_workable_days = min(30.0, days_covered)
        _logger.info("Workable days!")
        _logger.info(max_workable_days)
        # a) Ausencias ya registradas (is_leave = True en work_hours)
        for work_entry_type_id, hours in work_hours.items():
            work_entry_type = self.env['hr.work.entry.type'].browse(work_entry_type_id)
            if work_entry_type.is_leave:
                if work_entry_type.code != 'LEAVE90':
                    days = round(hours / hours_per_day, 5) if hours_per_day else 0
                    day_rounded = self._round_days(work_entry_type, days)
                    leave_days += day_rounded
                else:
                    days = round(hours / hours_per_day, 5) if hours_per_day else 0
                    day_rounded = self._round_days(work_entry_type, days)
    
        # b) Ausencias NO JUSTIFICADAS (días laborables sin entrada)
        unjustified_days = self._get_unjustified_absence_days(hours_per_day)
        #leave_days += unjustified_days  # ← ¡Esto es clave!
        _logger.info("Injustificadas")
        _logger.info(unjustified_days)
        # === Paso 2: Generar líneas de asistencia ===
        for work_entry_type_id, hours in work_hours_ordered:
            work_entry_type = self.env['hr.work.entry.type'].browse(work_entry_type_id)
            days = round(hours / hours_per_day, 5) if hours_per_day else 0
            if work_entry_type_id == biggest_work:
                days += add_days_rounding
            day_rounded = self._round_days(work_entry_type, days)
            add_days_rounding += (days - day_rounded)
    
            if work_entry_type.code == 'WORK100':
                    # Nómina normal: 30 días base, menos días fuera de contrato, menos ausencias
                    effective_base = max_workable_days - leave_days
                    day_rounded = max(0, round(effective_base, 5))
                    day_rounded = self._round_days(work_entry_type, day_rounded)
            if work_entry_type.code in ['OVERTIME_EVENING', 'OVERTIME_NIGHT', 'OVERTIME']:
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
    
        # === Paso 3: Agregar la línea de ausencia no justificada (si aplica) ===
        if unjustified_days > 0:
            unjustified_type = self.env['hr.work.entry.type'].search([('code', '=', 'UNJUSTIFIED')], limit=1)
            if unjustified_type:
                res.append({
                    'sequence': unjustified_type.sequence or 999,
                    'work_entry_type_id': unjustified_type.id,
                    'number_of_days': unjustified_days,
                    'number_of_hours': unjustified_days * hours_per_day,
                })
                _logger.info("Agregada ausencia no justificada: %d días", unjustified_days)
            else:
                _logger.warning("Tipo 'UNJUSTIFIED' no encontrado; ausencia no agregada.")
    
        # Ordenar y retornar
        work_entry_type = self.env['hr.work.entry.type']
        return sorted(res, key=lambda d: work_entry_type.browse(d['work_entry_type_id']).sequence)

    def _get_unjustified_absence_days(self, hours_per_day):
        """Devuelve el número de días laborables SIN entrada de trabajo DENTRO de la vigencia del contrato."""
        employee = self.employee_id
        contract = self.contract_id
        calendar = contract.resource_calendar_id or employee.resource_calendar_id or self.env.company.resource_calendar_id
    
        if not (calendar and employee.resource_id):
            return 0
        _logger.info("Calculo de ausencia")
        _logger.info(calendar)
        # → Usar rango efectivo del contrato en lugar del período completo de la nómina
        effective_start, effective_end = self._get_contract_effective_dates()
        _logger.info(effective_start)
        _logger.info(effective_end)
        if effective_start > effective_end:
            return 0  # No hay días dentro del contrato
    
        tz_name = calendar.tz or 'UTC'
        tz = pytz.timezone(tz_name)
    
        dt_from = fields.Datetime.to_datetime(effective_start)
        dt_to = fields.Datetime.to_datetime(effective_end)
        local_from = tz.localize(dt_from.replace(hour=0, minute=0, second=0))
        local_to = tz.localize(dt_to.replace(hour=23, minute=59, second=59))
    
        # Días laborables según calendario DENTRO del rango efectivo
        intervals = calendar._work_intervals_batch(local_from, local_to, resources=employee.resource_id, tz=tz)
        att_intervals = list(intervals.get(employee.resource_id.id, []))
        _logger.info(intervals)
        #_logger.info(att_intervals)
        workable_dates = set()
        for start, stop, _ in intervals.get(employee.resource_id.id, []):
            d = start.date()
            while d <= stop.date():
                workable_dates.add(d)
                d += timedelta(days=1)
    
        if not workable_dates:
            return 0
        _logger.info(workable_dates)
        # Días con alguna entrada de trabajo (cualquier tipo) en el MISMO rango
        work_entries = self.env['hr.work.entry'].search([
            ('employee_id', '=', employee.id),
            ('active', '=', True),
            ('date_stop', '<=', effective_end),
            ('date_start', '>=', effective_start),
        ])
        _logger.info(work_entries)
        covered_dates = set()
        for we in work_entries:
            start_utc = we.date_start
            stop_utc = we.date_stop
            start_local = pytz.utc.localize(start_utc).astimezone(tz).date()
            stop_local = pytz.utc.localize(stop_utc).astimezone(tz).date()
            d = start_local
            while d <= stop_local:
                if effective_start <= fields.Date.from_string(str(d)) <= effective_end:
                    covered_dates.add(d)
                d += timedelta(days=1)
        _logger.info(covered_dates)
        unjustified = workable_dates - covered_dates
        return len(unjustified)


    def get_ausencias(self,res,hours_per_day):
        # === NUEVO: Detectar ausencias no justificadas ===
        employee = self.employee_id
        contract = self.contract_id
        calendar = contract.resource_calendar_id or employee.resource_calendar_id or self.env.company.resource_calendar_id
        
        if calendar and employee.resource_id:
            tz_name = calendar.tz or 'UTC'
            tz = pytz.timezone(tz_name)
        
            # Normalizar fechas a datetime con tz
            dt_from = fields.Datetime.to_datetime(self.date_from)
            dt_to = fields.Datetime.to_datetime(self.date_to)
            local_from = tz.localize(dt_from.replace(hour=0, minute=0, second=0))
            local_to = tz.localize(dt_to.replace(hour=23, minute=59, second=59))
        
            # 1. Días laborables según calendario (sin feriados)
            intervals = calendar._work_intervals_batch(local_from, local_to, resources=employee.resource_id)
            workable_dates = set()
            for start, stop, _ in intervals.get(employee.resource_id.id, []):
                d = start.date()
                while d <= stop.date():
                    workable_dates.add(d)
                    d += timedelta(days=1)
        
            # 2. Días con alguna entrada de trabajo
            work_entries = self.env['hr.work.entry'].search([
                ('employee_id', '=', employee.id),
                ('date_stop', '>=', self.date_from),
                ('date_start', '<=', self.date_to),
            ])
            covered_dates = set()
            for we in work_entries:
                start_utc = we.date_start
                stop_utc = we.date_stop
                start_local = pytz.utc.localize(start_utc).astimezone(tz).date()
                stop_local = pytz.utc.localize(stop_utc).astimezone(tz).date()
                d = start_local
                while d <= stop_local:
                    covered_dates.add(d)
                    d += timedelta(days=1)
        
            # 3. Días no justificados
            unjustified = workable_dates - covered_dates
            unjustified_days = len(unjustified)
        
            if unjustified_days > 0:
                # Buscar o crear un tipo de entrada para "Ausencia no justificada"
                unjustified_type = self.env['hr.work.entry.type'].search([('code', '=', 'UNJUSTIFIED')], limit=1)
                if not unjustified_type:
                    # Opcional: crearlo si no existe (mejor hacerlo desde interfaz)
                    _logger.warning("No se encontró tipo de entrada 'UNJUSTIFIED'")
                else:
                    hours = unjustified_days * hours_per_day
                    attendance_line = {
                        'sequence': unjustified_type.sequence or 999,
                        'work_entry_type_id': unjustified_type.id,
                        'number_of_days': unjustified_days,
                        'number_of_hours': hours,
                    }
                    res.append(attendance_line)
                    _logger.info("Agregada ausencia no justificada: %d días", unjustified_days)
                
            

    
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

    def _get_contract_effective_dates(self):
        """Devuelve (effective_start, effective_end) del contrato dentro del período de la nómina."""
        payslip_start = self.date_from_events
        payslip_end = self.date_to_events
        contract = self.contract_id
    
        if not contract:
            return payslip_start, payslip_end
    
        contract_start = contract.date_start
        contract_end = contract.date_end or payslip_end
    
        effective_start = max(payslip_start, contract_start)
        effective_end = min(payslip_end, contract_end)
    
        if effective_start > effective_end:
            # Contrato totalmente fuera del período → rango vacío
            return payslip_start, payslip_start - timedelta(days=1)  # rango inválido
    
        return effective_start, effective_end

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


    def generate_ministerio_planilla_excel_report(self):
        if not self:
            return
    
        first_slip = self[0]
        first_month = first_slip.date_from.month
        first_year = first_slip.date_from.year
    
        for slip in self:
            if slip.date_from.month != first_month or slip.date_from.year != first_year:
                raise UserError("Todos los payslips deben pertenecer al mismo mes y año. "
                                  "El payslip de %(employee)s está en %(month)s/%(year)s, "
                                  "pero se esperaba %(expected_month)s/%(expected_year)s." % {
                    'employee': slip.employee_id.name,
                    'month': slip.date_from.month,
                    'year': slip.date_from.year,
                    'expected_month': first_month,
                    'expected_year': first_year,
                })
        company = self[0].company_id
        date_from = self[0].date_from
        date_to = self[0].date_to
        year = date_from.year
        month = date_from.strftime('%B').capitalize()

        year = date_from.year
        month_num = date_from.month
        num_days_in_month = monthrange(year, month_num)[1]
    
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Planilla Mensual')
    
        # Configurar hoja A4 horizontal
        worksheet.set_landscape()
        worksheet.set_paper(9)  # A4
    
        # Formatos
        bold = workbook.add_format({'bold': True})
        bold_center = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter'})
        center = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
        normal = workbook.add_format({'border': 1})
        footer = workbook.add_format({'italic': True})
    
        # ===== ENCABEZADO =====
        worksheet.merge_range('A1:K1', f'Razon Social: {company.name}', bold)
        worksheet.merge_range('L1:R1', 'Nro.Patronal IPS:', bold)
    
        worksheet.merge_range('A2:K2', f'Empleador: {company.name}', bold)
        worksheet.merge_range('L2:R2', 'Nro.Patronal MTESS:', bold)
    
        worksheet.merge_range('A3:K3', 'Actividad: Analisis Clinicos', bold)  # Ajustar si necesario
        worksheet.merge_range('L3:R3', f'RUC: {company.vat or ""}', bold)
    
        worksheet.merge_range('A4:K4', f'Domicilio: {company.street or ""}', bold)
        worksheet.merge_range('L4:R4', f'Telefono: {company.phone or ""}', bold)
    
        worksheet.write('A5', 'Año:', bold)
        worksheet.write('B5', str(year), bold)
        worksheet.merge_range('L5:R5', 'Pagina:', bold)
    
        worksheet.write('A6', 'Mes:', bold)
        worksheet.write('B6', month, bold)
        worksheet.merge_range('L6:R6', f'Correo: {company.email or ""}', bold)
    
        # Salto visual
        current_row = 8
    
        # ===== ENCABEZADOS DE COLUMNA =====
        day_headers = [str(d) for d in range(1, num_days_in_month + 1)]
        
        # Iniciales de los días
        DAY_INITIALS = ['L', 'M', 'X', 'J', 'V', 'S', 'D']
        day_initials = []
        for day in range(1, num_days_in_month + 1):
            d = date(year, month_num, day)
            day_initials.append(DAY_INITIALS[d.weekday()])
        
        # Estructura de bloques finales
        final_sections = [
            ('SALARIO', 2),
            ('HORAS EXTRAS', 6),
            ('BENEFICIOS SOCIALES', 4),
            ('Total General', 1),
        ]
        
        # Construir listas
        basic_cols = 3
        main_headers = ['Nro. Orden', 'C.I.', 'Apellidos y Nombre'] + day_headers
        sub_headers = ['', '', ''] + day_initials
        
        start_final = basic_cols + num_days_in_month
        main_header_positions = []
        current_col = start_final
        
        # Agregar subtítulos reales
        sub_headers.extend([
            'Forma pago','Importe Unitario', 'Días Trab.','Hora Trab.','Importe',
            'Cant. 50%', 'Cant. 100%', 'Cant. 130%',
            'Imp. 50%', 'Imp. 100%', 'Imp. 130%',
            'Vacación', 'Bonif. Fam.', 'Aguinaldo', 'Otros',
            'Total'
        ])
        
        # Preparar posiciones para merge
        for title, width in final_sections:
            main_header_positions.append((current_col, current_col + width - 1))
            current_col += width
        
        # Escribir encabezados
        for col in range(basic_cols + num_days_in_month):
            worksheet.write(current_row, col, main_headers[col], bold_center)
        
        # Fusionar bloques finales
        for (start, end), (title, _) in zip(main_header_positions, final_sections):
            worksheet.merge_range(current_row, start, current_row, end, title, bold_center)
        
        current_row += 1
        
        # Subtítulos
        for col, val in enumerate(sub_headers):
            worksheet.write(current_row, col, val, bold_center)
        
        current_row += 1
        # ===== MAPEO DE CÓDIGOS (ajusta según tus reglas salariales) =====
        CODE_MAP = {
            'VACACIONES': 'V',
            'FERIADO': 'F',
            'DOMINGO': 'D',
            'PERMISO': 'P',
            'REPOSO': 'R',
            # Agrega más si usas otros códigos
        }
    
        # Función auxiliar: determinar código para un día
        def get_day_code(entries, day_date):
            # Buscar en worked_days_line_ids
            for entry in entries:
                _logger.info(f"Discriminado {entry}- {entry.date_start.date()}")
                _logger.info(f"Fecha: {day_date}")
                if entry.date_start.date() == day_date:
                    _logger.info(f"Dia de trabajo {entry}")
                    if entry.work_entry_type_id.code == 'LEAVE120':
                        return 'V'
                    if entry.work_entry_type_id.code == 'LEAVE110':
                        return 'P'
                    if entry.work_entry_type_id.code == 'WORK100':
                        return '8'
            return ''  # Ausente o no registrado
        # ===== DATOS DE EMPLEADOS =====
        for idx, slip in enumerate(self, 1):
            employee = slip.employee_id
            ci = employee.identification_id or ''
            name = employee.name or ''
            work_entries = self.get_work_entries(employee,slip.date_from,slip.date_to)
            _logger.info(work_entries)
            # Rellenar días del mes
            days_data = []
            current = date_from
            while current <= date_to:
                day_code = get_day_code(work_entries, current)
                if day_code == '' and current.weekday() == 6:  # 6 = domingo en Python (lunes=0, ..., domingo=6)
                    day_code = 'D'
                days_data.append(day_code)
                current += timedelta(days=1)
            # Obtener montos (ajusta códigos según tu nómina)
            def get_amount(code):
                line = slip.line_ids.filtered(lambda l: l.code == code)
                return line[0].amount if line else 0.0
            def get_qty(code):
                line = slip.worked_days_line_ids.filtered(lambda l: l.code == code)
                if line.code == 'WORK100':
                    return line[0].number_of_days if line else 0.0
                return line[0].number_of_hours if line else 0.0
            salario = get_amount('BASIC')
            salario_dia = slip.contract_id.wage / 30 if slip.contract_id.wage else 0
            
            he_50_qty = get_qty('OVERTIME_EVENING') or 0
            he_100_qty = get_qty('WORK100') or 0
            hours_worked = he_100_qty * 8
            he_130_qty = get_qty('OVERTIME_NIGHT') or 0
            he_50_amt = get_amount('HEX50')
            he_100_amt = get_amount('HNOC30')
            he_130_amt = get_amount('HNOC30')
            
            vacacion = get_amount('VACACIONES')
            bonif_fam = get_amount('BONIF_FAMILIAR')
            aguinaldo = get_amount('AGUINALDO')
            otros = 0.0  # o suma de otros beneficios
    
            total_general = get_amount('NET')
            # Construir fila
            row = [
                idx,
                ci,
                name,
                *days_data,
                'M',
                salario_dia,
                he_100_qty,
                hours_worked,
                salario,
                he_50_qty, '', he_130_qty,
                he_50_amt, '', he_130_amt,
                vacacion, bonif_fam, aguinaldo,otros,
                total_general,
            ]
    
            # Escribir fila
            for col, val in enumerate(row):
                if col < 3 or 3 <= col <= 33:  # Nro, CI, Nombre, Días 1-31
                    worksheet.write(current_row, col, val, center)
                else:
                    if isinstance(val, float) and val == 0.0:
                        val = ''
                    worksheet.write(current_row, col, val, normal)
    
            current_row += 1
        day_start_col = 3
        day_end_col = 2 + num_days_in_month  # ej: si 30 días → col 32
        
        # Ajustar anchos
        worksheet.set_column(0, 0, 10)   # Nro
        worksheet.set_column(1, 1, 12)   # CI
        worksheet.set_column(2, 2, 25)   # Nombre
        worksheet.set_column(day_start_col, day_end_col, 4)  # Días
        
        # El resto (datos finales)
        final_start_col = day_end_col + 1
        total_cols = len(main_headers)
        if total_cols > final_start_col:
            worksheet.set_column(final_start_col, total_cols - 1, 10)
        # Cerrar y devolver
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())
        output.close()
    
        filename = f"Planilla_Ministerio_{year}_{month}.xlsx"
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


    def get_work_entries(self, employee, initial_date=False, stop_date=False):
        domain = [('employee_id', '=', employee.id)]
        if initial_date:
            # Asumiendo que 'initial_date' es una fecha y quieres filtrar por fecha de inicio o similar
            # Ajusta el campo de fecha según tu modelo (por ejemplo: 'date_start', 'date', etc.)
            domain += [('date_start', '>=', initial_date)]
        if stop_date:
            # Asumiendo que 'initial_date' es una fecha y quieres filtrar por fecha de inicio o similar
            # Ajusta el campo de fecha según tu modelo (por ejemplo: 'date_start', 'date', etc.)
            domain += [('date_stop', '<=', stop_date)]
        work_entries = self.env['hr.work.entry'].search(domain)
        return work_entries