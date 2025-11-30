# -*- coding: utf-8 -*-
from odoo import models, fields, api
from dateutil.relativedelta import relativedelta

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
    replacement = fields.Many2one("hr.leave", string="Reemplazante")
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


    def _get_durations(self, check_leave_type=True, resource_calendar=None):
        """
        Sobrescribe el cálculo de duración para usar días calendario completos.
        Ej: del 1 al 16 de un mes = 16 días.
        """
        result = {}
        for leave in self:
            if not leave.date_from or not leave.date_to:
                result[leave.id] = (0, 0)
                continue

            # Convertir a fechas (ignorar hora)
            start_date = leave.date_from.date()
            end_date = leave.date_to.date()

            # Calcular días calendario: incluye ambos extremos
            days = (end_date - start_date).days + 1
            hours = days * 24  # opcional: si necesitas horas

            result[leave.id] = (days, hours)

        return result