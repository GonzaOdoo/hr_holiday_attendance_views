# models/leave_liquidation_wizard.py

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta
import logging

_logger = logging.getLogger(__name__)
class LeaveLiquidationWizard(models.TransientModel):
    _name = 'leave.liquidation.wizard'
    _description = 'Wizard to Liquidate Unused Leave Days'

    allocation_id = fields.Many2one('hr.leave.allocation', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee',string='Empleado', related='allocation_id.employee_id', readonly=True)
    available_to_liquidate = fields.Float(related='allocation_id.available_to_liquidate',string='Disponibles a liquidar', readonly=True)
    month_year = fields.Date(
        string="Mes en nómina",
        required=True,
        help="Select the payroll month (e.g., 01/10/2025 for October 2025). The leave will be placed on the last working day of this month."
    )
    leave_type_id = fields.Many2one(
        'hr.leave.type',
        string="Tipo de tiempo",
        required=True,
        domain="[('has_valid_allocation', '=', False), ('requires_allocation', '=', 'no')]"
    )

    @api.onchange('month_year')
    def _onchange_month_year(self):
        if self.month_year:
            # Asegurar que la fecha sea el primer día del mes (para cálculos)
            self.month_year = self.month_year.replace(day=1)

    def action_liquidate(self):
        self.ensure_one()
        if self.available_to_liquidate <= 0:
            raise UserError(_("No days available to liquidate."))
    
        # Asegurar que el mes_year sea el primer día del mes (ya lo haces en onchange)
        start_of_month = self.month_year.replace(day=1)
    
        # Calcular la fecha final: N días corridos a partir del primer día
        num_days = int(self.available_to_liquidate)
        date_from = start_of_month
        date_to = start_of_month + relativedelta(days=num_days - 1)  # -1 porque el primer día cuenta como día 1
    
        # Crear el leave
        leave = self.env['hr.leave'].create({
            'employee_id': self.employee_id.id,
            'holiday_status_id': self.leave_type_id.id,
            'request_date_from': date_from,
            'request_date_to': date_to,
            'number_of_days': self.available_to_liquidate,  # aunque Odoo lo recalcula, lo forzamos por claridad
            'name': _("Liquidación de días no usados"),
        })
        
        # Validar para que se consuman los días
        leave.action_validate()
    
        self.allocation_id.message_post(
            body=_("Liquidation leave created: %(days)s days from %(date_from)s to %(date_to)s.",
                   days=self.available_to_liquidate, date_from=date_from, date_to=date_to)
        )
    
        return {'type': 'ir.actions.act_window_close'}