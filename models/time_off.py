# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from dateutil.relativedelta import relativedelta
from datetime import datetime
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
        """
        Calcula la duración de la ausencia en días y horas hábiles,
        usando el calendario laboral del empleado.
        """
        result = {}
        for leave in self:
            if not leave.date_from or not leave.date_to:
                result[leave.id] = (0, 0)
                continue
    
            # Obtener el calendario: preferir el del empleado, luego el parámetro, luego ninguno
            calendar = leave.employee_id.resource_calendar_id or resource_calendar
            if not calendar:
                # Si no hay calendario, fallback a cálculo simple (pero con precisión horaria)
                duration_days = (leave.date_to - leave.date_from).days
                duration_hours = (leave.date_to - leave.date_from).total_seconds() / 3600
                result[leave.id] = (round(duration_days + (duration_hours % 24) / 24, 2), round(duration_hours, 2))
                continue
    
            # Asegurar zonas horarias coherentes (Odoo usa UTC en campos datetime)
            tz = pytz.timezone(leave.employee_id.tz or 'UTC')
            start_dt = leave.date_from.astimezone(tz)
            end_dt = leave.date_to.astimezone(tz)
    
            # Calcular horas hábiles según el calendario
            work_hours = calendar.get_work_hours_count(
                start_dt=start_dt,
                end_dt=end_dt,
                compute_leaves=False  # ya estás en un leave, no contar otros leaves
            )
    
            # Convertir horas hábiles a días hábiles (asumiendo jornada diaria estándar)
            # Ej: si el calendario tiene 8h/día, 8h = 1 día útil
            hours_per_day = calendar.hours_per_day or 8.0
            work_days = work_hours / hours_per_day if hours_per_day else 0
    
            result[leave.id] = (round(work_days, 2), round(work_hours, 2))
    
        return result


    def action_validate(self,check_state=True):
        res = super().action_validate(check_state=True)

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