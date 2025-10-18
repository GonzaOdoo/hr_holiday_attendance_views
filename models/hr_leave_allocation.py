from odoo import _, api, fields, models
from datetime import date
from odoo.tools.sql import SQL
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)
class HrLeaveAllocation(models.Model):
    _inherit = 'hr.leave.allocation'

    liquidation_date = fields.Date(
    string="Liquidation Date",
    compute='_compute_liquidation_date',
    store=True,
    help="Date set 6 months after the start date (date_from)."
    )
    
    available_to_liquidate = fields.Float(
        string="Available to Liquidate",
        compute='_compute_available_to_liquidate',
        store=True,
        help="Total allocated days minus days already taken."
    )

    requires_liquidation = fields.Boolean(
        string="Requires Liquidation",
        compute='_compute_requires_liquidation',
        store=True,
        help="Checked if the liquidation date has passed and there are days available to liquidate."
    )

    liquidation_leave_type_id = fields.Many2one(
        'hr.leave.type',
        string="Tipo de tiempo para liquidación",
        help="Tipo de ausencia que se usará para registrar liquidaciones de días no tomados.",
    )
    
    @api.depends('date_from')
    def _compute_liquidation_date(self):
        for allocation in self:
            if allocation.date_from:
                allocation.liquidation_date = allocation.date_from + relativedelta(months=6)
            else:
                allocation.liquidation_date = False
    
    @api.depends(
        'max_leaves',
        'leaves_taken',
        'liquidation_leave_type_id',
        'employee_id',
        'date_from',
        'date_to'
    )
    def _compute_available_to_liquidate(self):
        for allocation in self:
            if not allocation.liquidation_leave_type_id or not allocation.employee_id:
                allocation.available_to_liquidate = max(0.0, allocation.max_leaves - allocation.leaves_taken)
                continue
    
            # Buscar liquidaciones aprobadas en el período de la asignación
            liquidated_leaves = self.env['hr.leave'].search([
                ('employee_id', '=', allocation.employee_id.id),
                ('holiday_status_id', '=', allocation.liquidation_leave_type_id.id),
                ('state', 'in', ['confirm', 'validate']),
                ('request_date_to', '>=', allocation.date_from),
                ('request_date_from', '<=', allocation.date_to),
            ])
    
            total_liquidated = sum(liquidated_leaves.mapped('number_of_days'))
            allocation.available_to_liquidate = max(
                0.0,
                allocation.max_leaves - allocation.leaves_taken - total_liquidated
            )

    @api.depends('liquidation_date', 'available_to_liquidate')
    def _compute_requires_liquidation(self):
        today = fields.Date.today()
        for allocation in self:
            allocation.requires_liquidation = (
                allocation.liquidation_date
                and allocation.liquidation_date <= today
                and allocation.available_to_liquidate > 0
            )


    def action_open_liquidation_wizard(self):
        self.ensure_one()
        if not self.requires_liquidation:
            raise UserError(_("This allocation does not require liquidation."))
    
        return {
            'name': _('Liquidate Unused Leave Days'),
            'type': 'ir.actions.act_window',
            'res_model': 'leave.liquidation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_allocation_id': self.id,
                'default_employee_id': self.employee_id.id,
            }
        }

    def action_recompute_liquidation_data(self):
        """
        Fuerza la recomputación de los campos relacionados con la liquidación.
        Útil cuando se modifican/cancelan/eliminan ausencias de liquidación manualmente.
        """
        self._compute_available_to_liquidate()
        self._compute_requires_liquidation()
        # Opcional: mostrar notificación
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Actualización completada'),
                'message': _('Los datos de liquidación han sido actualizados.'),
                'type': 'info',
                'sticky': False,
            }
        }