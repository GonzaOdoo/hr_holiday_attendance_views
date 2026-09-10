# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from dateutil.relativedelta import relativedelta
from datetime import datetime,time, timedelta
from odoo.exceptions import UserError,ValidationError
import pytz
import logging

_logger = logging.getLogger(__name__)

class HrLeave(models.Model):
    _inherit = 'hr.leave'

    apply_discount = fields.Selection(
    [('yes', 'Con Descuento'), ('no', 'Sin descuento')],
    string='Descuento'
    )

    tipo_enfermedad = fields.Selection(
        [('ips', 'Con reposo IPS'), ('privado', 'Con reposo privado')],
        string='Tipo de reposo'
    )

    balance_info = fields.Char(
        string="Saldo Disponible",
        compute="_compute_balance_info",
        store=False,
        readonly=True
    )
    allocation_id = fields.Many2one('hr.leave.allocation', string="Asignación de origen")
    replacement = fields.Many2one("hr.employee", string="Reemplazante")
    reason_text = fields.Text("Motivo del permiso", tracking=True)
    shift_start = fields.Float("Hora de entrada")
    shift_end = fields.Float("Hora de salida")
    calendar_days = fields.Many2one('resource.calendar',string='Horario definido')
    shift_change = fields.Boolean(string='Cambio de horario',related='holiday_status_id.shift_change')
    attendance_id = fields.Many2one(
        'hr.attendance',
        string='Asistencia relacionada',
        compute="_compute_overtime_attendance",
        ondelete='set null',
        store = True,
    )
    overtime_day = fields.Float(
        string="Horas Extra Diurnas",
        compute="_compute_overtime_from_request",
        store=False,
    )
    
    overtime_night = fields.Float(
        string="Horas Extra Nocturnas",
        compute="_compute_overtime_from_request",
        store=False,
    )
    
    night_hours = fields.Float(
        string="Recargo Nocturno",
        compute="_compute_overtime_from_request",
        store=False,
    )
    
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
        readonly=True,
    )
    
    overtime_day_amount = fields.Monetary(
        string="Monto HED",
        compute="_compute_overtime_from_request",
        currency_field="currency_id",
    )
    
    overtime_night_amount = fields.Monetary(
        string="Monto HEN",
        compute="_compute_overtime_from_request",
        currency_field="currency_id",
    )
    
    night_hours_amount = fields.Monetary(
        string="Monto Recargo Nocturno",
        compute="_compute_overtime_from_request",
        currency_field="currency_id",
    )
    
    total_overtime_amount = fields.Monetary(
        string="Total Horas Extra",
        compute="_compute_overtime_from_request",
        currency_field="currency_id",
    )
    
    total_with_night_amount = fields.Monetary(
        string="Total con Recargo",
        compute="_compute_overtime_from_request",
        currency_field="currency_id",
    )
    attendance_overtime_day = fields.Float(
        string="HED en asistencia",
        related='attendance_id.overtime_day',
        readonly=True,
    )
    
    attendance_overtime_night = fields.Float(
        string="HEN en asistencia",
        related='attendance_id.overtime_night',
        readonly=True,
    )
    
    attendance_night_hours = fields.Float(
        string="Recargo nocturno en asistencia",
        related='attendance_id.night_hours',
        readonly=True,
    )
    
    attendance_overtime_hours = fields.Float(
        string="Total HE en asistencia",
        related='attendance_id.overtime_hours',
        readonly=True,
    )
    
    attendance_validated_overtime_hours = fields.Float(
        string="HE validadas en asistencia",
        related='attendance_id.validated_overtime_hours',
        readonly=True,
    )
    
    attendance_overtime_from_leave = fields.Boolean(
        string="Asistencia modificada por solicitud",
        related='attendance_id.overtime_from_leave',
        readonly=True,
    )
    related_overtime_leave_ids = fields.One2many(
        related='attendance_id.overtime_leave_ids',
        string='Solicitudes relacionadas',
        readonly=True,
    )
    
    @api.depends(
        'employee_id',
        'request_date_from',
        'holiday_status_id',
        'holiday_status_id.is_overtime',
    )
    def _compute_overtime_attendance(self):
        Attendance = self.env['hr.attendance']
    
        for leave in self:
            leave.attendance_id = False
    
            if (
                not leave.holiday_status_id
                or not leave.holiday_status_id.is_overtime
                or not leave.employee_id
                or not leave.request_date_from
            ):
                continue
    
            date_from = fields.Date.to_date(leave.request_date_from)
            date_to = date_from + relativedelta(days=1)
    
            attendance = Attendance.search([
                ('employee_id', '=', leave.employee_id.id),
                ('check_in', '>=', fields.Datetime.to_datetime(date_from)),
                ('check_in', '<', fields.Datetime.to_datetime(date_to)),
            ], order='check_in asc', limit=1)
    
            leave.attendance_id = attendance

    @api.depends(
        'employee_id',
        'request_date_from',
        'request_hour_from',
        'request_hour_to',
        'attendance_id',
    )
    def _compute_overtime_from_request(self):
    
        for leave in self:
            leave.overtime_day = 0.0
            leave.overtime_night = 0.0
            leave.night_hours = 0.0
    
            leave.overtime_day_amount = 0.0
            leave.overtime_night_amount = 0.0
            leave.night_hours_amount = 0.0
            leave.total_overtime_amount = 0.0
            leave.total_with_night_amount = 0.0
            if not leave.holiday_status_id.is_overtime:
                continue
            if (
                not leave.employee_id
                or not leave.request_date_from
                or leave.request_hour_from is False
                or leave.request_hour_to is False
            ):
                continue
    
            date = fields.Date.to_date(leave.request_date_from)
    
            tz = pytz.timezone(
                leave.employee_id.tz or 'America/Asuncion'
            )
    
            # Convertir Float de Odoo a horas/minutos
            hour_from = int(leave.request_hour_from)
            minute_from = int(
                round((leave.request_hour_from - hour_from) * 60)
            )
    
            hour_to = int(leave.request_hour_to)
            minute_to = int(
                round((leave.request_hour_to - hour_to) * 60)
            )
    
            start = tz.localize(
                datetime.combine(
                    date,
                    time(hour_from, minute_from)
                )
            )
    
            end = tz.localize(
                datetime.combine(
                    date,
                    time(hour_to, minute_to)
                )
            )
    
            # Si cruza medianoche
            if end <= start:
                end += timedelta(days=1)
            attendance = leave.attendance_id
            
            if attendance:
                scheduled_check_in = attendance.scheduled_check_in
                scheduled_check_out = attendance.scheduled_check_out
            
                check_in = attendance.check_in
                check_out = attendance.check_out
            
                # Convertir los horarios de la asistencia a la zona horaria
                # del empleado.
                if scheduled_check_in:
                    scheduled_check_in = fields.Datetime.to_datetime(
                        scheduled_check_in
                    ).astimezone(tz)
            
                if scheduled_check_out:
                    scheduled_check_out = fields.Datetime.to_datetime(
                        scheduled_check_out
                    ).astimezone(tz)
            
                if check_in:
                    check_in = fields.Datetime.to_datetime(
                        check_in
                    ).astimezone(tz)
            
                if check_out:
                    check_out = fields.Datetime.to_datetime(
                        check_out
                    ).astimezone(tz)
            
                # =========================================================
                # HORAS EXTRA ANTES DE LA ENTRADA PROGRAMADA
                # =========================================================
                if (
                    scheduled_check_in
                    and start < scheduled_check_in
                ):
                    # La solicitud está pidiendo horas antes
                    # de la entrada normal.
                    #
                    # Si marcó después de la hora solicitada,
                    # comenzamos desde su marcación real.
                    if check_in and check_in > start:
                        start = check_in
            
                # =========================================================
                # HORAS EXTRA DESPUÉS DE LA SALIDA PROGRAMADA
                # =========================================================
                if (
                    scheduled_check_out
                    and end > scheduled_check_out
                ):
                    # La solicitud está pidiendo horas después
                    # de la salida normal.
                    #
                    # Si marcó salida antes de la hora solicitada,
                    # terminamos en su marcación real.
                    if check_out and check_out < end:
                        end = check_out
            if start >= end:
                continue
            # Dividir intervalo según tu lógica
            day_hours, night_hours = self.env[
                'hr.attendance'
            ]._split_interval_day_night(
                start,
                end
            )
    
            leave.overtime_day = round(day_hours, 2)
            leave.overtime_night = round(night_hours, 2)
    
            # El recargo nocturno es independiente de las horas
            # extra nocturnas.
            leave.night_hours = round(night_hours, 2)
    
            # Buscar contrato
            contract = leave.employee_id.contract_ids.filtered(
                lambda c: c.state == 'open'
            )[:1]
            
            _logger.info(leave.employee_id)
            _logger.info(contract)
            if not contract or not contract.wage:
                continue
    
            hours_per_day = 8
            hourly_rate = contract.wage / 30 / hours_per_day
    
            day_rate = hourly_rate * 1.5
            night_rate = hourly_rate * 2.0
    
            leave.overtime_day_amount = (
                leave.overtime_day * day_rate
            )
    
            leave.overtime_night_amount = (
                leave.overtime_night * night_rate
            )
    
            leave.night_hours_amount = (
                leave.night_hours * hourly_rate * 0.30
            )
    
            leave.total_overtime_amount = (
                leave.overtime_day_amount
                + leave.overtime_night_amount
            )
    
            leave.total_with_night_amount = (
                leave.total_overtime_amount
                + leave.night_hours_amount
            )
    @api.depends('holiday_status_id', 'employee_id', 'request_date_from')
    def _compute_balance_info(self):
        for leave in self:
            if not leave.holiday_status_id or not leave.employee_id or not leave.request_date_from:
                leave.balance_info = ""
                continue
            data = leave.holiday_status_id.get_allocation_data(leave.employee_id, leave.request_date_from)
            if leave.employee_id in data and data[leave.employee_id]:
                vals = data[leave.employee_id][0][1]
                leave.balance_info = (
                    f"Total asignado: {vals['max_leaves']} días | "
                    f"Tomados: {vals['leaves_taken']} | "
                    f"Restantes: {vals['virtual_remaining_leaves']} días"
                )
            else:
                leave.balance_info = "Sin asignación disponible"

    @api.model
    def _get_last_work_day_of_month(self, date_in_month):
        # Obtener el último día del mes
        next_month = date_in_month + relativedelta(months=1)
        last_day = next_month - relativedelta(days=1)

        # Retroceder hasta encontrar un día laborable (asumiendo calendario del empleado)
        employee = self.env.context.get('employee_id')
        if not employee:
            return last_day

        calendar = employee.resource_calendar_id or self.env.company.resource_calendar_id
        while last_day.weekday() >= 5:  # sáb-dom (ajustar si el calendario es diferente)
            last_day -= relativedelta(days=1)

        # Mejor: usar el calendario real
        # Buscar el último día hábil usando el calendario
        from datetime import timedelta
        current = datetime.combine(last_day, fields.Datetime.now().time())
        while current.date() >= date_in_month.replace(day=1):
            if calendar._works_on_date(current.date()):
                return current.date()
            current -= timedelta(days=1)
        return date_in_month.replace(day=1)  # fallback

    @api.constrains('holiday_status_id', 'shift_start', 'shift_end')
    def _check_shift_hours(self):
        for rec in self:
            if rec.holiday_status_id.shift_change:

                if not rec.shift_start or not rec.shift_end:
                    raise ValidationError(_(
                        "Debe completar la hora de entrada y salida para este tipo de tiempo personal."
                    ))

                duration = rec.shift_end - rec.shift_start

                if duration < 0:
                    duration += 24

                if duration < 7 or duration > 9:
                    raise ValidationError(_(
                        "El rango horario debe estar entre 7 y 9 horas."
                    ))

    def _get_durations(self, check_leave_type=True, resource_calendar=None):
    
        result = {}
    
        for leave in self:
            if not leave.date_from or not leave.date_to:
                result[leave.id] = (0, 0)
                continue
    
            calendar = leave.employee_id.resource_calendar_id or resource_calendar
    
            if not calendar:
                duration_hours = (
                    leave.date_to - leave.date_from
                ).total_seconds() / 3600
    
                duration_days = duration_hours / 8.0
    
                result[leave.id] = (
                    round(duration_days, 2),
                    round(duration_hours, 2),
                )
                continue
    
            tz = pytz.timezone(leave.employee_id.tz or 'UTC')
    
            start_dt = leave.date_from.astimezone(tz)
            end_dt = leave.date_to.astimezone(tz)
    
            # Horas laborales reales
            work_hours = calendar.get_work_hours_count(
                start_dt=start_dt,
                end_dt=end_dt,
                compute_leaves=True,
            )
    
            # Licencias por horas
            if leave.leave_type_request_unit == 'hour':
                work_days = 0
    
            else:
                # Contar días laborales reales
                work_days = 0
    
                current_day = start_dt.date()
                end_day = end_dt.date()
    
                while current_day <= end_day:
    
                    day_start = tz.localize(datetime.combine(
                        current_day,
                        datetime.min.time()
                    ))
    
                    day_end = tz.localize(datetime.combine(
                        current_day,
                        datetime.max.time()
                    ))
    
                    daily_hours = calendar.get_work_hours_count(
                        start_dt=day_start,
                        end_dt=day_end,
                        compute_leaves=True,
                    )
    
                    if daily_hours > 0 or leave.holiday_status_id.count_non_working_days:
                        work_days += 1
    
                    current_day += timedelta(days=1)
    
            result[leave.id] = (
                round(work_days, 2),
                round(work_hours, 2),
            )
    
        return result


    def action_validate(self,check_state=True):
        res = super().action_validate(check_state=True)
        if self.holiday_status_id.is_overtime:
            self._apply_overtime_to_attendance()
        for leave in self:
            # Solo si es cambio de horario
            if leave.holiday_status_id.shift_change:
                if not leave.calendar_days:
                    raise UserError("Debe elegir una planilla horaria antes de confirmar un cambio de turno")
                employee = leave.employee_id
                self.env['hr.employee.shift.change'].create({
                    'employee_id': employee.id,
                    'leave_id': leave.id,
                    'calendar_id': leave.calendar_days.id,
                    'date_start': leave.date_from,
                    'date_end': leave.date_to,
                })
        return res

    def action_refuse(self):
        res = super().action_refuse()
        for leave in self:
            if leave.holiday_status_id.shift_change:
                leave.employee_id.write({
                    'extra_calendar_id': False,
                    'extra_calendar_start': False,
                    'extra_calendar_end': False,
                })
    
        return res
        
    def action_validate_change_calendar(self):
        self.ensure_one()
        #self._apply_overtime_to_attendance()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Aprobar cambio de horario',
            'res_model': 'hr.leave.shift.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_leave_id': self.id,
                'default_calendar_days': self.calendar_days.id,
                'default_shift_start': self.shift_start,
                'default_shift_end': self.shift_end,
                'default_employee_id':self.employee_id.id,
                'default_date_from':self.date_from,
                'default_date_to':self.date_to,
            }
        }

    def _get_leaves_on_public_holiday(self):
        leaves = super()._get_leaves_on_public_holiday()
        return leaves.filtered(
            lambda leave: not leave.holiday_status_id.count_non_working_days
        )

    def _apply_overtime_to_attendance(self):
        Attendance = self.env['hr.attendance']
    
        for leave in self:
            if not leave.employee_id or not leave.request_date_from:
                continue
    
            date = fields.Date.to_date(leave.request_date_from)
            date_to = date + relativedelta(days=1)
    
            attendance = Attendance.search([
                ('employee_id', '=', leave.employee_id.id),
                ('check_in', '>=', fields.Datetime.to_datetime(date)),
                ('check_in', '<', fields.Datetime.to_datetime(date_to)),
            ], limit=1)
    
            if not attendance:
                continue
    
            # Asociamos esta solicitud a la asistencia.
            attendance = leave.attendance_id
    
            # Tomamos todas las solicitudes validadas de esta asistencia.
            leaves = attendance.overtime_leave_ids.filtered(
                lambda l: l.state == 'validate'
            )
    
            overtime_day = sum(leaves.mapped('overtime_day'))
            overtime_night = sum(leaves.mapped('overtime_night'))
            night_hours = sum(leaves.mapped('night_hours'))
    
            total_overtime = overtime_day + overtime_night
            if total_overtime < 0.5:
                raise ValidationError("No se puede aprobar un permiso de horas extras con menos de 30 minutos")
    
            attendance.write({
                'overtime_from_leave': True,
                'overtime_day': overtime_day,
                'overtime_night': overtime_night,
                'night_hours': night_hours,
                'overtime_hours': total_overtime,
                'validated_overtime_hours': total_overtime,
            })
            attendance.action_approve_overtime()

