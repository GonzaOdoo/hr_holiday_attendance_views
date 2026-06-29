from odoo import models, fields, api, _
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta
import pytz

class HrLeaveLiquidationWizard(models.TransientModel):
    _name = 'hr.leave.liquidation.wizard'
    _description = 'Leave Liquidation Wizard'

    report_id = fields.Many2one(
        'hr.leave.allocation.report',
    )
    report_ids = fields.Many2many(
        'hr.leave.allocation.report',
        string='Asignaciones',
    )

    liquidation_date = fields.Date(
        string='Fecha de liquidación',
        required=True,
        default=fields.Date.today,
    )
    employee_id = fields.Many2one(
        'hr.employee',
        related='report_id.employee_id',
        readonly=True,
    )

    available_to_liquidate = fields.Float(
        string='Días a liquidar',
        related='report_id.available_to_liquidate',
        readonly=True,
    )
    is_massive = fields.Boolean(
        compute='_compute_is_massive',
    )
    batch_id = fields.Many2one(
        'hr.payslip.run',
        string='Lote de nómina',
    )
    
    @api.depends('report_ids')
    def _compute_is_massive(self):
        for wizard in self:
            wizard.is_massive = len(wizard.report_ids) > 1

    
    def action_create_payslip(self):
        self.ensure_one()

        reports = self.report_ids or self.report_id

        if not reports:
            raise UserError(_("No hay registros seleccionados."))

        payslips = self.env['hr.payslip']

        for report in reports:
            liquidation = self._create_liquidation_from_report(report)
            payslip = self._create_payslip_from_liquidation(liquidation)
            payslips |= payslip
        for payslip in payslips:
            payslip.compute_sheet()

        self.batch_id.write({
            'slip_ids': [(4, slip.id) for slip in payslips]
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.payslip.run',
            'res_id': self.batch_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


    def _create_liquidation_from_report(self, report):
        emp = report.employee_id

        start = (
            emp.x_studio_inicio
            or emp.first_contract_date
            or emp.create_date.date()
        )

        today = fields.Date.today()
        years_worked = relativedelta(today, start).years

        period_start = start + relativedelta(years=years_worked)
        period_end = (
            period_start
            + relativedelta(years=1)
            - relativedelta(days=1)
        )

        allocation = self.env['hr.leave.allocation'].search([
            ('employee_id', '=', emp.id),
            ('state', 'in', ['confirm', 'validate', 'validate1']),
            ('date_from', '=', period_start),
            ('date_to', '=', period_end),
        ], limit=1)

        if not allocation:
            raise UserError(_(
                "No se encontró asignación para %s."
            ) % emp.name)

        days = report.available_to_liquidate

        if days <= 0:
            raise UserError(_(
                "El empleado %s no tiene días a liquidar."
            ) % emp.name)

        date_start = self.liquidation_date
        date_end = self._calculate_leave_end_date(
            employee=emp,
            date_start=date_start,
            days=days,
        )

        liquidation = self.env['hr.leave.liquidation'].create({
            'report_id': report.id,
            'employee_id': emp.id,
            'allocation_id': allocation.id,
            'date_start': date_start,
            'date_end': date_end,
            'days': days,
        })

        allocation._compute_available_to_liquidate()
        allocation._compute_requires_liquidation()

        return liquidation
    
    def _create_payslip_from_liquidation(self, liquidation):
        employee = liquidation.employee_id

        contract = employee.contract_id
        if not contract:
            raise UserError(_(
                "El empleado %s no tiene contrato activo."
            ) % employee.name)

        structure_type = contract.structure_type_id
        if not structure_type:
            raise UserError(_(
                "El contrato %s no tiene tipo de estructura."
            ) % contract.display_name)

        structure = structure_type.struct_ids.filtered(
            lambda s: s.is_holiday_liquidation
        )[:1]

        if not structure:
            raise UserError(_(
                "No existe estructura de liquidación "
                "de vacaciones para %s."
            ) % structure_type.display_name)

        payslip = self.env['hr.payslip'].create({
            'name': _("Liquidación vacaciones %s") % employee.name,
            'employee_id': employee.id,
            'contract_id': contract.id,
            'struct_id': structure.id,
            'date_from': liquidation.date_start,
            'date_to': liquidation.date_end,
            'leave_liquidation_id': liquidation.id,
        })

        return payslip

    def action_confirm(self):
        self.ensure_one()

        reports = self.report_ids or self.report_id

        if not reports:
            raise UserError(_("No hay registros para liquidar."))

        liquidations = self.env['hr.leave.liquidation']

        for report in reports:
            liquidation = self._create_liquidation_from_report(report)
            liquidations |= liquidation

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Liquidación creada'),
                'message': _(
                    'Se crearon %s liquidaciones.'
                ) % len(liquidations),
                'type': 'success',
            }
        }


    def _calculate_leave_end_date(self, employee, date_start, days):
        """
        Calcula la fecha final real de vacaciones
        considerando únicamente días laborables.
        """
        calendar = employee.resource_calendar_id
    
        if not calendar:
            # Fallback simple
            return date_start + relativedelta(days=int(days) - 1)
    
        tz = pytz.timezone(employee.tz or 'UTC')
    
        remaining_days = int(days)
        current_day = date_start
    
        while remaining_days > 0:
    
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
    
            # Solo cuenta si es laborable
            if daily_hours > 0:
                remaining_days -= 1
    
            if remaining_days > 0:
                current_day += timedelta(days=1)
    
        return current_day