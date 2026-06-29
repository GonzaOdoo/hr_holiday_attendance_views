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
    payslip_id = fields.Many2one( 'hr.payslip', string='Recibo de nómina', copy=False, ) 
    has_payslip = fields.Boolean( compute='_compute_has_payslip', store=False, )

    @api.depends('payslip_id') 
    def _compute_has_payslip(self): 
        for record in self: 
            record.has_payslip = bool(record.payslip_id)

    def action_create_payslip(self):
        self.ensure_one()
    
        if self.payslip_id:
            raise UserError(_("La liquidación ya tiene un recibo relacionado."))
    
        employee = self.employee_id
        contract = employee.contract_id
        if not contract:
            raise UserError(_("El empleado no tiene contrato activo."))
    
        structure = contract.structure_type_id.struct_ids.filtered(
            lambda s: s.is_holiday_liquidation
        )[:1]
        if not structure:
            raise UserError(_("No se encontró una estructura de liquidación de vacaciones."))
    
        payslip = self.env['hr.payslip'].create({
            'name': f'Liquidación vacaciones {employee.name}',
            'employee_id': employee.id,
            'contract_id': contract.id,
            'struct_id': structure.id,
            'date_from': self.date_start,
            'date_to': self.date_end,
            'leave_liquidation_id': self.id,
        })
    
        self.payslip_id = payslip.id
    
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.payslip',
            'res_id': payslip.id,
            'view_mode': 'form',
            'target': 'current',
        }