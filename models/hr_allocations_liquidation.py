from odoo import models, fields, api

class HrLeaveLiquidation(models.Model):
    _name = 'hr.leave.liquidation'
    _description = 'Liquidación de vacaciones'
    _order = 'date_start desc'

    report_id = fields.Many2one(
        'hr.leave.allocation.report',
        required=True,
    )

    employee_id = fields.Many2one(
        'hr.employee',
        required=True,
        index=True,
    )

    allocation_id = fields.Many2one(
        'hr.leave.allocation',
        required=True,
        ondelete='cascade',
    )

    date_start = fields.Date(
        required=True,
    )

    date_end = fields.Date(
        required=False,
    )

    days = fields.Float(
        required=True,
    )

    company_id = fields.Many2one(
        related='employee_id.company_id',
        store=True,
        readonly=True,
    )