from odoo import models, fields, api, _
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta

class HrLeaveLiquidationWizard(models.TransientModel):
    _name = 'hr.leave.liquidation.wizard'
    _description = 'Leave Liquidation Wizard'

    report_id = fields.Many2one(
        'hr.leave.allocation.report',
        required=True,
    )

    liquidation_date = fields.Date(
        string='Fecha de liquidación',
        required=True,
        default=fields.Date.today,
    )

    def action_confirm(self):
        self.ensure_one()

        report = self.report_id
        emp = report.employee_id

        start = (
            emp.x_studio_inicio
            or emp.first_contract_date
            or emp.create_date.date()
        )

        today = fields.Date.today()
        years_worked = relativedelta(today, start).years

        period_start = start + relativedelta(years=years_worked)
        period_end = period_start + relativedelta(years=1) - relativedelta(days=1)

        allocation = self.env['hr.leave.allocation'].search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ['confirm', 'validate', 'validate1']),
            ('date_from', '=', period_start),
            ('date_to', '=', period_end),
        ], limit=1)

        if not allocation:
            raise UserError(_("No se encontró la asignación."))

        days = allocation.available_to_liquidate

        if days <= 0:
            raise UserError(_("No hay días para liquidar."))

        date_start = self.liquidation_date
        date_end = date_start + relativedelta(days=int(days) - 1)

        self.env['hr.leave.liquidation'].create({
            'employee_id': emp.id,
            'allocation_id': allocation.id,
            'date_start': date_start,
            'date_end': date_end,
            'days': days,
        })

        allocation._compute_available_to_liquidate()
        allocation._compute_requires_liquidation()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Liquidación creada'),
                'message': _(
                    'Se registró la liquidación de %s días.'
                ) % days,
                'type': 'success',
            }
        }