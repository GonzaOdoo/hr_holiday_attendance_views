
from odoo import api, fields, models, _
from odoo.exceptions import UserError,ValidationError


class DepartureReason(models.Model):
    _inherit = "hr.payslip"

    def action_payslip_done(self):
        res = super().action_payslip_done()

        for payslip in self:
            if payslip.struct_id.is_final_liquidation:
                employee = payslip.employee_id
                contract = payslip.contract_id

                # Finalizar el contrato con la fecha de baja.
                if contract and payslip.last_day:
                    contract.write({
                        "date_end": payslip.last_day,
                    })

                # Actualizar el estado laboral del empleado.
                employee.write({
                    "final_liquidation_status": "inactive",
                })

                # Archivar al empleado.
                if employee.active:
                    employee.with_context(
                        allow_final_liquidation_archive=True,
                    ).action_archive()

        return res