# -*- coding: utf-8 -*-
from odoo import models, fields, api
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