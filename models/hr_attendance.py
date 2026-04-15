# -*- coding: utf-8 -*-

from calendar import monthrange
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from operator import itemgetter
from random import randint
from odoo import models, fields, api
from datetime import datetime,time, timedelta
import pytz
from pytz import timezone, UTC
import logging
import math

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

    scheduled_check_in = fields.Datetime(string='Hora programada de entrada', compute='_compute_scheduled_attendance_times', store=True)
    scheduled_check_out = fields.Datetime(
        string='Hora programada de salida', 
        compute='_compute_scheduled_attendance_times', 
        store=True
    )
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

    warning_shift_pending = fields.Boolean(
        string="Cambio pendiente",
        compute="_compute_shift_warnings",
        store=False
    )
    night_hours = fields.Float(
        string='Recargo nocturno',
        compute='_compute_night_hours',
        store=True
    )

    @api.depends('check_in', 'check_out')
    def _compute_night_hours(self):
    
        def overlap(start1, end1, start2, end2):
            start = max(start1, start2)
            end = min(end1, end2)
            return max((end - start).total_seconds(), 0)
    
        for att in self:
            att.night_hours = 0.0
    
            if not att.check_in or not att.check_out:
                continue
    
            tz_name = att.employee_id.tz or 'America/Asuncion'
            tz = pytz.timezone(tz_name)
    
            check_in = fields.Datetime.to_datetime(att.check_in).astimezone(tz)
            check_out = fields.Datetime.to_datetime(att.check_out).astimezone(tz)
    
            total_seconds = 0
    
            day = check_in.date()
            last_day = check_out.date()
    
            while day <= last_day:
    
                night1_start = tz.localize(datetime.combine(day, time(0, 0)))
                night1_end = tz.localize(datetime.combine(day, time(6, 0)))
    
                night2_start = tz.localize(datetime.combine(day, time(20, 0)))
                night2_end = tz.localize(datetime.combine(day, time(23, 59, 59)))
    
                total_seconds += overlap(check_in, check_out, night1_start, night1_end)
                total_seconds += overlap(check_in, check_out, night2_start, night2_end)
    
                day += timedelta(days=1)
    
            att.night_hours = total_seconds / 3600.0
        
    @api.depends('check_in', 'employee_id')
    def _compute_shift_warnings(self):
        for att in self:
            att.warning_shift_pending = False
    
            if not att.employee_id or not att.check_in:
                continue
    
            leave = self.env['hr.leave'].search([
                ('employee_id', '=', att.employee_id.id),
                ('holiday_status_id.shift_change', '=', True),
                ('state', 'not in', ['validate', 'refuse']),
                ('date_from', '<=', att.check_in),
                ('date_to', '>=', att.check_in),
            ], limit=1)
    
            if leave:
                att.warning_shift_pending = True

    @api.depends('late_minutes', 'late_status')
    def _compute_confirmed_late_minutes(self):
        for attendance in self:
            if attendance.late_status == 'approved':
                attendance.confirmed_late_minutes = attendance.late_minutes
            else:
                attendance.confirmed_late_minutes = 0.0

    @api.depends('employee_id', 'check_in','employee_id.shift_change_ids')
    def _compute_scheduled_attendance_times(self):
        for attendance in self:
            _logger.info("Computing late!")
            if not attendance.employee_id or not attendance.check_in:
                attendance.scheduled_check_in = False
                attendance.scheduled_check_out = False
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
                attendance.scheduled_check_out = False
                continue
    
            calendar = contract.resource_calendar_id
            employee = attendance.employee_id
            # ✅ Usar zona horaria de Paraguay por defecto si no está definida
            local_tz = timezone(employee.tz or 'America/Asuncion')
    
            # Convertir check_in a zona local para comparar con el calendario
            check_in_local = attendance.check_in.replace(tzinfo=UTC).astimezone(local_tz)
            day_start = local_tz.localize(datetime.combine(check_in_local.date(), time.min))
            day_end = day_start + timedelta(days=2)
            #day_end = local_tz.localize(datetime.combine(check_in_local.date(), time.max))

            _logger.info("Days!!")
            _logger.info(day_start)
            _logger.info(day_end)
            candidates = []
            # Obtener intervalos laborales del día (excluye descansos automáticamente)
            intervals = employee._employee_attendance_intervals(
                day_start.astimezone(UTC),
                day_end.astimezone(UTC),
                lunch=False
            )
            normal_intervals = contract.resource_calendar_id._attendance_intervals_batch(
                day_start.astimezone(UTC),
                day_end.astimezone(UTC),
                employee.resource_id
            ).get(employee.resource_id.id, [])
            check_date = attendance.check_in.date()

            normal_intervals = sorted(normal_intervals, key=lambda x: x[0])

            for interval in normal_intervals:
                start_local = interval[0].astimezone(local_tz)
                # eliminar intervalos antes de las 04:00 del mismo día
                if (
                    start_local.date() == check_in_local.date()
                    and start_local.hour < 4
                ):
                    continue
            
                candidates.append(interval)

            shift_change = self.env['hr.employee.shift.change'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'approved'),
            ], order='date_start desc', limit=10)
            _logger.info(shift_change)
            shift_change = shift_change.filtered(
                lambda s: s.date_start.date() <= check_date <= s.date_end.date()
            )[:1]
            _logger.info(shift_change)
            extra_intervals = []
            
            if shift_change:
                calendar = shift_change.calendar_id
                extra_intervals = calendar._attendance_intervals_batch(
                    day_start.astimezone(UTC),
                    day_end.astimezone(UTC),
                    employee.resource_id
                ).get(employee.resource_id.id, [])
                _logger.info("Extra intervals!!!!")
                _logger.info(extra_intervals)
                for interval in extra_intervals:
                    start_local = interval[0].astimezone(local_tz)
                    # eliminar intervalos antes de las 04:00 del mismo día
                    if (
                        start_local.date() == check_in_local.date()
                        and start_local.hour < 4
                    ):
                        continue
                
                    candidates.append(interval)
                
            
            intervals_list = sorted(list(intervals), key=lambda x: x[0])
            
            # ✅ Obtener primer intervalo para entrada programada
            if intervals_list:
                # Buscar el intervalo más cercano al check_in
                _logger.info(intervals_list)
                check_in_local = attendance.check_in.replace(tzinfo=UTC).astimezone(local_tz)
                
                closest_interval = None
                smallest_diff = None
                _logger.info("Candidates!!!")
                _logger.info(candidates)
                for interval in candidates:
                    start = interval[0]
                    end = interval[1]
                
                    if start <= check_in_local <= end:
                        closest_interval = interval
                        break
                
                # fallback: usar el primer intervalo del día
                if not closest_interval and candidates:
                    closest_interval = candidates[0]
                
                if closest_interval:
                    scheduled_in_local = closest_interval[0]
                    scheduled_out_local = closest_interval[1]
                    intervals_sorted = sorted(candidates, key=lambda x: x[0])
                    _logger.info("Calculo de salida")
                    _logger.info(intervals_sorted)
                    shift_end = closest_interval[1]
                    prev_end = closest_interval[1]
                    _logger.info(shift_end)
                    
                    for interval in intervals_sorted:
                        start = interval[0]
                        end = interval[1]
                        
                        if start <= closest_interval[0]:
                            continue
                    
                        gap_hours = (start - prev_end).total_seconds() / 3600
                    
                        # tolerancia pequeña (descansos o división de calendario)
                        if gap_hours <= 1.5:
                            shift_end = end
                            prev_end = end
                            continue
                    
                        # gap grande → terminó el turno
                        break
                    _logger.info(shift_end) 
                
                    attendance.scheduled_check_in = scheduled_in_local.astimezone(UTC).replace(tzinfo=None)
                    attendance.scheduled_check_out = shift_end.astimezone(UTC).replace(tzinfo=None)
                else:
                    attendance.scheduled_check_in = False
                    attendance.scheduled_check_out = False
            else:
                attendance.scheduled_check_in = False
    
            # ✅ Obtener último intervalo para salida programada

                
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


    @api.depends('worked_hours','employee_id.shift_change_ids')
    def _compute_overtime_hours(self):
        if self.env.context.get('skip_overtime_compute'):
            return
        atts = self.filtered(lambda r: r._name == 'hr.attendance')
        fallback_atts = self.env['hr.attendance']

        for att in atts:
            att.overtime_hours = 0.0
    
            if not att.check_in or not att.check_out or not att.employee_id:
                fallback_atts |= att
                continue
    
            employee = att.employee_id
            calendar = employee.resource_calendar_id
    
            if not calendar:
                fallback_atts |= att
                continue
    
            tz = pytz.timezone(calendar.tz or 'UTC')
    
            check_in = pytz.utc.localize(att.check_in).astimezone(tz)
            check_out = pytz.utc.localize(att.check_out).astimezone(tz)
    
            range_start = check_in - timedelta(hours=8)
            range_end = check_out + timedelta(hours=8)
    
            intervals = calendar._attendance_intervals_batch(
                range_start,
                range_end,
                employee.resource_id
            ).get(employee.resource_id.id, [])
    
            intervals = list(intervals)
    
            if not intervals:
                fallback_atts |= att
                continue
    
            sched_in, sched_out = self._get_full_shift_interval(att, intervals)
    
            if not sched_in or not sched_out:
                fallback_atts |= att
                continue
    
            # 🔴 NUEVA REGLA: validar cercanía al turno
            diff_hours = abs((check_in - sched_in).total_seconds() / 3600.0)
    
            if diff_hours > 3:
                fallback_atts |= att
                continue
            # =========================
            # ✅ TU LÓGICA CUSTOM
            # =========================
            grace_hours = 0.5
            
            extra_before = 0.0
            extra_after = 0.0
            
            # Entrada anticipada
            entry_diff = (sched_in - check_in).total_seconds() / 3600.0
            if entry_diff > grace_hours:
                extra_before = entry_diff
            
            # Salida tardía
            exit_diff = (check_out - sched_out).total_seconds() / 3600.0
            if exit_diff > grace_hours:
                extra_after = exit_diff
            
            att.overtime_hours = extra_before + extra_after
        # =========================
        # 🔁 FALLBACK A ODOO
        # =========================
        if fallback_atts:
            super(HrContract, fallback_atts)._compute_overtime_hours()
        
            for att in fallback_atts:
                if att.overtime_hours < 0:
                    att.overtime_hours = 0.0

    def _normalize_interval(self, interval):
        """
        Devuelve (start, end) independientemente del tipo
        """
        if hasattr(interval, 'start'):
            return interval.start, interval.stop
        else:
            return interval[0], interval[1]
            
    def _get_full_shift_interval(self, att, intervals):
        if not intervals:
            return None, None
    
        intervals = list(intervals)
    
        # 🔹 Normalizar y ordenar
        normalized = []
        for i in intervals:
            start, end = self._normalize_interval(i)
            normalized.append((start, end))
    
        normalized = sorted(normalized, key=lambda x: x[0])
    
        # 🔹 TZ consistente
        tz = normalized[0][0].tzinfo
        check_in = pytz.utc.localize(att.check_in).astimezone(tz)
    
        # 🔹 Encontrar intervalo actual
        current = None
        for start, end in normalized:
            if start <= check_in <= end:
                current = (start, end)
                break
    
        if not current:
            current = normalized[0]
    
        shift_start, shift_end = current
    
        # 🔹 Unir consecutivos
        tolerance = timedelta(minutes=65)
    
        for start, end in normalized:
            if start >= shift_end and (start - shift_end) <= tolerance:
                shift_end = end
    
        return shift_start, shift_end

    @api.depends('check_in', 'check_out','employee_id.shift_change_ids')
    def _compute_worked_hours(self):
        """ Computes the worked hours of the attendance record.
            The worked hours of resource with flexible calendar is computed as the difference
            between check_in and check_out, without taking into account the lunch_interval"""
        for attendance in self:
            if attendance.check_out and attendance.check_in and attendance.employee_id:
                attendance.worked_hours = attendance._get_worked_hours_in_range(attendance.check_in, attendance.check_out)
            else:
                attendance.worked_hours = False