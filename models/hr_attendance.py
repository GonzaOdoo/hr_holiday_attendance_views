# -*- coding: utf-8 -*-
from odoo import models, fields, api
from datetime import datetime,time, timedelta
import pytz
from pytz import timezone, UTC
import logging

_logger = logging.getLogger(__name__)

class HrContract(models.Model):
    _inherit = 'hr.attendance'

    is_guard = fields.Boolean('Es guardia')
    nocturno = fields.Boolean(
        'Nocturno', 
        compute='_compute_nocturno',
        store=True  # No se guarda en la base de datos
    )
    # Campos derivados (solo lectura, calculados desde los originales)
    check_in_date = fields.Date(
        string="Fecha de Entrada",
        compute='_compute_check_in_date',
        store=True,
        tracking=True
    )
    check_in_time = fields.Char(
        string="Hora de Entrada",
        compute='_compute_check_in_time',
        store=True,
        tracking=True
    )

    check_out_date = fields.Date(
        string="Fecha de Salida",
        compute='_compute_check_out_date',
        store=True,
        tracking=True
    )
    check_out_time = fields.Char(
        string="Hora de salida",
        compute='_compute_check_out_time',
        store=True,
        tracking=True
    )
    overtime_day = fields.Float(
        string="Horas Extra Diurnas",
        compute='_compute_overtime_split',
        store=True,
        tracking=True
    )
    overtime_night = fields.Float(
        string="Horas Extra Nocturnas",
        compute='_compute_overtime_split',
        store=True,
        tracking=True
    )

    scheduled_check_in = fields.Datetime(string='Hora programada de entrada', compute='_compute_scheduled_check_in', store=True)
    late_minutes = fields.Float(string='Minutos de retraso', compute='_compute_late_minutes', store=True)
    is_late = fields.Boolean(string='¿Llegó tarde?', compute='_compute_is_late', store=True)

    late_status = fields.Selection([
    ('to_approve', 'Por aprobar'),
    ('approved', 'Aprobado (Penalizar)'),
    ('refused', 'Rechazado (No penalizar)')
    ], string="Estado de llegada tardía", default='to_approve', tracking=True)
    
    confirmed_late_minutes = fields.Float(
        string="Minutos tarde confirmados",
        compute='_compute_confirmed_late_minutes',
        store=True
    )

    @api.depends('late_minutes', 'late_status')
    def _compute_confirmed_late_minutes(self):
        for attendance in self:
            if attendance.late_status == 'approved':
                attendance.confirmed_late_minutes = attendance.late_minutes
            else:
                attendance.confirmed_late_minutes = 0.0

    @api.depends('employee_id', 'check_in')
    def _compute_scheduled_check_in(self):
        for attendance in self:
            if not attendance.employee_id or not attendance.check_in:
                attendance.scheduled_check_in = False
                continue
    
            # Obtener contrato vigente en la fecha de check_in
            contract = self.env['hr.contract'].search([
                ('employee_id', '=', attendance.employee_id.id),
                ('state', '=', 'open'),
                ('date_start', '<=', attendance.check_in.date()),
                '|',
                ('date_end', '=', False),
                ('date_end', '>=', attendance.check_in.date())
            ], limit=1)
    
            if not contract or not contract.resource_calendar_id:
                attendance.scheduled_check_in = False
                continue
    
            calendar = contract.resource_calendar_id
            employee = attendance.employee_id
            local_tz = timezone(employee.tz or 'UTC')
    
            # Convertir check_in a zona local para comparar con el calendario
            check_in_local = attendance.check_in.replace(tzinfo=UTC).astimezone(local_tz)
            day_start = datetime.combine(check_in_local.date(), time.min).replace(tzinfo=local_tz)
            day_end = datetime.combine(check_in_local.date(), time.max).replace(tzinfo=local_tz)
    
            # Obtener intervalos laborales del día
            intervals = calendar._work_intervals_batch(
                day_start,
                day_end,
                resources=employee.resource_id,
                tz=local_tz
            )[employee.resource_id.id]
    
            # Tomar el primer intervalo como hora de entrada programada
            first_interval = next(iter(intervals), None)
            if first_interval:
                # ✅ Convertir a UTC antes de guardar (¡IMPORTANTE!)
                scheduled_local = first_interval[0]  # datetime con tz local
                scheduled_utc = scheduled_local.astimezone(UTC)  # convertir a UTC
                attendance.scheduled_check_in = scheduled_utc.replace(tzinfo=None)  # guardar como naive UTC
            else:
                attendance.scheduled_check_in = False

    @api.depends('scheduled_check_in', 'check_in')
    def _compute_late_minutes(self):
        for attendance in self:
            if attendance.scheduled_check_in and attendance.check_in:
                # Convertir scheduled_check_in (almacenado como UTC naive) a zona local
                local_tz = timezone(attendance.employee_id.tz or 'UTC')
                scheduled_naive_utc = attendance.scheduled_check_in  # UTC naive
                scheduled_local = scheduled_naive_utc.replace(tzinfo=UTC).astimezone(local_tz).replace(tzinfo=None)
    
                # Convertir check_in real a zona local
                check_in_local = attendance.check_in.replace(tzinfo=UTC).astimezone(local_tz).replace(tzinfo=None)
    
                delta = check_in_local - scheduled_local
                attendance.late_minutes = max(0, delta.total_seconds() / 60.0)
            else:
                attendance.late_minutes = 0.0

    @api.depends('late_minutes')
    def _compute_is_late(self):
        for attendance in self:
            if not attendance.employee_id:
                attendance.is_late = False
                continue
    
            # Obtener el umbral de la compañía del empleado
            company = attendance.employee_id.company_id or self.env.company
            late_threshold = company.attendance_late_threshold_minutes or 10
    
            attendance.is_late = attendance.late_minutes > late_threshold

    # -------------------------------
    # Métodos de cálculo (compute)
    # -------------------------------

    @api.depends('check_in')
    def _compute_check_in_date(self):
        for record in self:
            if record.check_in:
                user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                check_in_local = pytz.UTC.localize(record.check_in).astimezone(user_tz)
                record.check_in_date = check_in_local.date()
            else:
                record.check_in_date = False

    @api.depends('check_in')
    def _compute_check_in_time(self):
        for record in self:
            if record.check_in:
                user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                check_in_local = pytz.UTC.localize(record.check_in).astimezone(user_tz)
                record.check_in_time = f"{check_in_local.hour:02d}:{check_in_local.minute:02d}"
            else:
                record.check_in_time = False  # o "" si prefieres cadena vacía

    @api.depends('check_out')
    def _compute_check_out_date(self):
        for record in self:
            if record.check_out:
                user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                check_out_local = pytz.UTC.localize(record.check_out).astimezone(user_tz)
                record.check_out_date = check_out_local.date()
            else:
                record.check_out_date = False

    @api.depends('check_out')
    def _compute_check_out_time(self):
        for record in self:
            if record.check_out:
                user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                check_out_local = pytz.UTC.localize(record.check_out).astimezone(user_tz)
                record.check_out_time = f"{check_out_local.hour:02d}:{check_out_local.minute:02d}"
            else:
                record.check_out_time = False


    @api.depends('check_out', 'validated_overtime_hours')
    def _compute_overtime_split(self):
        for record in self:
            if not record.check_out or not record.validated_overtime_hours:
                record.overtime_day = 0.0
                record.overtime_night = 0.0
                continue

            # Convertir check_out a zona horaria del usuario
            user_tz = pytz.timezone(self.env.user.tz or 'UTC')
            check_out_local = pytz.UTC.localize(record.check_out).astimezone(user_tz)

            # Total de horas extras a descontar (en timedelta)
            overtime_td = timedelta(hours=record.validated_overtime_hours)

            # Hora de inicio de las horas extras (check_out - overtime)
            overtime_start = check_out_local - overtime_td

            # Inicializar contadores
            total_day = 0.0   # Horas diurnas (06:00 - 20:00)
            total_night = 0.0 # Horas nocturnas (20:00 - 06:00)

            # Iterar por cada día en el rango de horas extras
            current = overtime_start
            while current < check_out_local:
                next_day = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

                # Definir límites del día actual
                day_start = current.replace(hour=6, minute=0, second=0, microsecond=0)
                night_start = current.replace(hour=20, minute=0, second=0, microsecond=0)
                day_end = next_day.replace(hour=6, minute=0, second=0, microsecond=0)

                # Asegurar que day_start y night_start estén en orden cronológico
                segments = []

                # Segmento 1: Desde current hasta las 20:00 (si aplica)
                if current < night_start:
                    end_segment1 = min(night_start, check_out_local)
                    if current < end_segment1:
                        segments.append((current, end_segment1, 'day'))

                # Segmento 2: Desde 20:00 hasta las 06:00 del día siguiente (si aplica)
                if current < day_end and (not segments or segments[-1][1] < day_end):
                    start_night = max(current, night_start)
                    end_night = min(day_end, check_out_local)
                    if start_night < end_night:
                        segments.append((start_night, end_night, 'night'))

                # Procesar segmentos
                for seg_start, seg_end, seg_type in segments:
                    seg_hours = (seg_end - seg_start).total_seconds() / 3600.0
                    if seg_type == 'day':
                        total_day += seg_hours
                    else:
                        total_night += seg_hours

                # Avanzar al siguiente día
                current = day_end

            # Asignar resultados
            record.overtime_day = round(total_day, 2)
            record.overtime_night = round(total_night, 2)
    
    @api.depends('check_in')
    def _compute_nocturno(self):
        for attendance in self:
            if attendance.check_in:
                # Convertir el datetime a hora local
                check_in_time = fields.Datetime.context_timestamp(
                    attendance, 
                    attendance.check_in
                ).time()
                
                # Verificar si está entre las 22:00 y 6:00
                attendance.nocturno = (
                    check_in_time.hour >= 22 or 
                    check_in_time.hour < 6
                )
            else:
                attendance.nocturno = False


    def action_approve_late(self):
        for attendance in self:
            if attendance.late_status != 'to_approve':
                continue
            attendance.write({
                'late_status': 'approved',
            })
    
    def action_refuse_late(self):
        for attendance in self:
            if attendance.late_status != 'to_approve':
                continue
            attendance.write({
                'late_status': 'refused',
            })