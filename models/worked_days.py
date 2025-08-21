# -*- coding: utf-8 -*-
from odoo import models

class HrPayslipWorkedDays(models.Model):
    _inherit = 'hr.payslip.worked_days'

    def _compute_amount(self):
        # === Tipos de entrada que vamos a manejar ===
        overtime_day_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME_EVENING')], limit=1)
        overtime_night_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME_NIGHT')], limit=1)
        guard_day_type = self.env['hr.work.entry.type'].search([('code', '=', 'GUARD_EVENING')], limit=1)
        guard_night_type = self.env['hr.work.entry.type'].search([('code', '=', 'GUARD_NIGHT')], limit=1)

        # Estructuras a excluir (ej. US)
        us_structures = self.env['hr.payroll.structure'].search([('code', '=', 'USMONTHLY')])

        # Separar los registros que manejamos aquí
        special_worked_days = self.env['hr.payslip.worked_days']
        other_worked_days = self.env['hr.payslip.worked_days']

        for wd in self:
            if wd.payslip_id.struct_id in us_structures:
                other_worked_days |= wd
                continue
            if wd.payslip_id.wage_type != 'hourly':
                other_worked_days |= wd
                continue
            if not wd.payslip_id.contract_id.hourly_wage:
                other_worked_days |= wd
                continue

            if wd.work_entry_type_id in (overtime_day_type, overtime_night_type, guard_day_type, guard_night_type):
                special_worked_days |= wd
            else:
                other_worked_days |= wd

        # === Calcular montos para casos especiales ===
        for wd in special_worked_days:
            contract = wd.payslip_id.contract_id
            hourly_wage = contract.hourly_wage
            hours = wd.number_of_hours
            is_paid = wd.is_paid

            amount = 0.0
            if is_paid:
                if wd.work_entry_type_id == overtime_day_type:
                    # HED = SH * 1.5
                    rate = hourly_wage * 1.5
                    amount = rate * hours

                elif wd.work_entry_type_id == overtime_night_type:
                    # HEN = (SH + 30% recargo) * 2 = SH * 2.6
                    rate = hourly_wage * 2.6
                    amount = rate * hours

                elif wd.work_entry_type_id == guard_day_type:
                    # GD = HED * 8 → 8 horas a tasa de HED
                    rate = hourly_wage * 1.5
                    amount = rate * 8.0  # pago fijo por guardia diurna

                elif wd.work_entry_type_id == guard_night_type:
                    # GN = HEN * 8 → 8 horas a tasa de HEN
                    rate = hourly_wage * 2.6
                    amount = rate * 8.0  # pago fijo por guardia nocturna

            wd.amount = amount

        # === Dejar el resto al cálculo original ===
        super(HrPayslipWorkedDays, other_worked_days)._compute_amount()