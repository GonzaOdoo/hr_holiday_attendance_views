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
        store=True,
        tracking=True
    )
    overtime_night = fields.Float(
        string="Horas Extra Nocturnas",
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
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, readonly=True)
    overtime_day_amount = fields.Monetary(string="Monto HED",compute='_compute_overtime_amount' ,store=False)
    overtime_night_amount = fields.Monetary(string="Monto HEN",compute='_compute_overtime_amount', store=False)
    night_hours_amount = fields.Monetary(
        string="Monto recargo nocturno",
        compute='_compute_overtime_amount',
        store=False
    )
    total_overtime_amount = fields.Monetary(
        string="Total horas extra",
        compute='_compute_overtime_amount',
        store=False
    )
    
    total_with_night_amount = fields.Monetary(
        string="Total con recargo nocturno",
        compute='_compute_overtime_amount',
        store=False
    )
    
    @api.depends('overtime_night','overtime_day','night_hours')
    def _compute_overtime_amount(self):
        for att in self:
            contract = att.employee_id.contract_id
            
            if not contract or not contract.wage:
                att.overtime_day_amount = 0
                att.overtime_night_amount = 0
                att.night_hours_amount = 0
                att.total_overtime_amount = 0
                att.total_with_night_amount = 0
                continue
            
            # 🔹 Valor hora (ajustable)
            hours_per_day = 8
            hourly_rate = contract.wage / 30 / hours_per_day
            
            # 🔹 Horas
            day_hours = att.overtime_day or 0
            night_hours_ot = att.overtime_night or 0
            night_hours = att.night_hours or 0
            
            # 🔹 Cálculo
            day_rate = hourly_rate * 1.5
            night_rate = hourly_rate * 2.0
            day_amount = day_rate * day_hours
            night_ot_amount = night_rate * night_hours_ot
            night_extra_amount = hourly_rate * 0.30 * night_hours
            
            att.overtime_day_amount = day_amount
            att.overtime_night_amount = night_ot_amount
            att.night_hours_amount = night_extra_amount
            #Totales
            att.total_overtime_amount = day_amount + night_ot_amount
            att.total_with_night_amount = att.total_overtime_amount + night_extra_amount

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

    @api.depends('employee_id', 'check_in', 'employee_id.shift_change_ids')
    def _compute_scheduled_attendance_times(self):
        for attendance in self:
    
            if not attendance.employee_id or not attendance.check_in:
                attendance.scheduled_check_in = False
                attendance.scheduled_check_out = False
                continue
    
            employee = attendance.employee_id
    
            # Obtener contrato vigente en la fecha
            contract = self.env['hr.contract'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'open'),
                ('date_start', '<=', attendance.check_in.date()),
                '|',
                ('date_end', '=', False),
                ('date_end', '>=', attendance.check_in.date())
            ], limit=1)
    
            if not contract:
                attendance.scheduled_check_in = False
                attendance.scheduled_check_out = False
                continue
    
            # Zona horaria del empleado
            local_tz = timezone(employee.tz or 'America/Asuncion')
    
            check_in_local = attendance.check_in.replace(tzinfo=UTC).astimezone(local_tz)
            check_date = check_in_local.date()
    
            day_start = local_tz.localize(datetime.combine(check_date, time.min))
            day_end = day_start + timedelta(days=2)
    
            # Buscar cambio de turno aprobado
            shift_change = self.env['hr.employee.shift.change'].search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'approved'),
            ], order='date_start desc', limit=10)
    
            shift_change = shift_change.filtered(
                lambda s: s.date_start.date() <= check_date <= s.date_end.date()
            )[:1]
    
            # ✅ Elegir calendario correcto
            calendar = shift_change.calendar_id if shift_change else contract.resource_calendar_id
    
            if not calendar:
                attendance.scheduled_check_in = False
                attendance.scheduled_check_out = False
                continue
    
            # Obtener intervalos del calendario elegido
            intervals = calendar._attendance_intervals_batch(
                day_start.astimezone(UTC),
                day_end.astimezone(UTC),
                employee.resource_id
            ).get(employee.resource_id.id, [])
    
            candidates = []
    
            for interval in sorted(intervals, key=lambda x: x[0]):
                start_local = interval[0].astimezone(local_tz)
    
                # eliminar intervalos antes de las 04:00 del mismo día
                if (
                    start_local.date() == check_date
                    and start_local.hour < 4
                ):
                    continue
    
                candidates.append(interval)
    
            if not candidates:
                attendance.scheduled_check_in = False
                attendance.scheduled_check_out = False
                continue
    
            # Buscar intervalo correspondiente al check_in
            closest_interval = None
    
            for interval in candidates:
                start = interval[0]
                end = interval[1]
    
                if start <= check_in_local <= end:
                    closest_interval = interval
                    break
    
            # fallback
            if not closest_interval:
                closest_interval = candidates[0]
    
            scheduled_in_local = closest_interval[0]
    
            # Calcular fin real del turno (unir bloques cercanos)
            intervals_sorted = sorted(candidates, key=lambda x: x[0])
    
            shift_end = closest_interval[1]
            prev_end = closest_interval[1]
    
            for interval in intervals_sorted:
                start = interval[0]
                end = interval[1]
    
                if start <= closest_interval[0]:
                    continue
    
                gap_hours = (start - prev_end).total_seconds() / 3600
    
                # tolerancia para descansos o divisiones del calendario
                if gap_hours <= 1.5:
                    shift_end = end
                    prev_end = end
                    continue
    
                break
    
            attendance.scheduled_check_in = scheduled_in_local.astimezone(UTC).replace(tzinfo=None)
            attendance.scheduled_check_out = shift_end.astimezone(UTC).replace(tzinfo=None)

                
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


    def _split_interval_day_night(self, start, end):
        total_day = 0.0
        total_night = 0.0
    
        current = start
    
        while current < end:
            # límites del día actual
            day_0 = current.replace(hour=0, minute=0, second=0, microsecond=0)
            day_6 = current.replace(hour=6, minute=0, second=0, microsecond=0)
            day_20 = current.replace(hour=20, minute=0, second=0, microsecond=0)
            next_day = day_0 + timedelta(days=1)
    
            segments = [
                (day_0, day_6, 'night'),
                (day_6, day_20, 'day'),
                (day_20, next_day, 'night'),
            ]
    
            for seg_start, seg_end, typ in segments:
                overlap_start = max(current, seg_start)
                overlap_end = min(end, seg_end)
    
                if overlap_start < overlap_end:
                    hours = (overlap_end - overlap_start).total_seconds() / 3600.0
                    if typ == 'day':
                        total_day += hours
                    else:
                        total_night += hours
    
            current = next_day
    
        return total_day, total_night
        
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


    @api.depends(
        'worked_hours',
        'check_in',
        'check_out',
        'scheduled_check_in',
        'scheduled_check_out'
    )
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
    
            # ✅ usar horario ya calculado
            if not att.scheduled_check_in or not att.scheduled_check_out:
                fallback_atts |= att
                continue
    
            employee = att.employee_id
            calendar = employee.resource_calendar_id
    
            tz = pytz.timezone(calendar.tz or 'UTC') if calendar else pytz.UTC
    
            check_in = pytz.utc.localize(att.check_in).astimezone(tz)
            check_out = pytz.utc.localize(att.check_out).astimezone(tz)
    
            sched_in = pytz.utc.localize(att.scheduled_check_in).astimezone(tz)
            sched_out = pytz.utc.localize(att.scheduled_check_out).astimezone(tz)
    
            # 🔴 validar cercanía al turno
            diff_hours = abs((check_in - sched_in).total_seconds() / 3600.0)
    
            if diff_hours > 6:
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
            day_hours = 0.0
            night_hours = 0.0

            # 🔹 Intervalo antes del turno
            if extra_before > 0:
                before_start = check_in
                before_end = sched_in
            
                d, n = self._split_interval_day_night(before_start, before_end)
                day_hours += d
                night_hours += n
            
            # 🔹 Intervalo después del turno
            if extra_after > 0:
                after_start = sched_out
                after_end = check_out
            
                d, n = self._split_interval_day_night(after_start, after_end)
                day_hours += d
                night_hours += n

            att.overtime_day = round(day_hours, 2)
            att.overtime_night = round(night_hours, 2)

            # 🔹 Descuento por tardanza
            late_hours = (att.late_minutes or 0.0) / 60.0
            
            if late_hours > 0:
                # Primero descontar de horas diurnas
                if att.overtime_day >= late_hours:
                    att.overtime_day -= late_hours
                    late_hours = 0.0
                else:
                    late_hours -= att.overtime_day
                    att.overtime_day = 0.0
            
                # Si todavía queda tardanza, descontar de nocturnas
                if late_hours > 0:
                    if att.overtime_night >= late_hours:
                        att.overtime_night -= late_hours
                    else:
                        att.overtime_night = 0.0
    
            att.overtime_hours = att.overtime_day + att.overtime_night
    
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