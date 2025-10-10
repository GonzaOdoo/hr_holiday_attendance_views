from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class HrSalaryAttachment(models.Model):
    _inherit = 'hr.salary.attachment'

    @api.depends("payslip_ids.state")
    def _compute_has_done_payslip(self):
        for record in self:
            has_done = any(p.state in ['done', 'paid'] for p in record.payslip_ids)
            record.has_done_payslip = has_done
            if has_done and record.state not in ('close', 'cancel'):
                record.state = 'close'
            