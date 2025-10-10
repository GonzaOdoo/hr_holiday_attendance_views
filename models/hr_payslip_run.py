from odoo import models, fields, api, Command
from datetime import date,datetime,timedelta
from dateutil.relativedelta import relativedelta
class HrContract(models.Model):
    _inherit = 'hr.payslip.run'

    date_from_events = fields.Date(
        string='Inicio Novedades',
        compute='_compute_date_events',
        store=True,
        readonly=True,
    )
    date_to_events = fields.Date(
        string='Fin Novedades',
        compute='_compute_date_events',
        store=True,
        readonly=True,
    )

    @api.depends('date_end')  # Solo depende de date_to, porque es nuestra referencia
    def _compute_date_events(self):
        for payslip in self:
            if payslip.date_end:
                # Fin de novedades: siempre el día 20 del mes de date_to
                date_to_events = payslip.date_end.replace(day=20)

                # Inicio de novedades: 21 del mes anterior
                date_from_events = date_to_events - relativedelta(months=1)
                date_from_events = date_from_events.replace(day=21)

                payslip.date_from_events = date_from_events
                payslip.date_to_events = date_to_events
            else:
                payslip.date_from_events = False
                payslip.date_to_events = False