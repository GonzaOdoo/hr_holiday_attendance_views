# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class ResCompany(models.Model):
    _inherit = 'res.company'

    ips = fields.Char('Patronal IPS')
    mtess = fields.Char('Patronal MTESS')
    attendance_late_threshold_minutes = fields.Float(
        string="Minutos para considerar llegada tarde",
        default=10.0
    )

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    attendance_late_threshold_minutes = fields.Float(
        string="Minutos para considerar llegada tarde",
        related='company_id.attendance_late_threshold_minutes',
        readonly=False,
        help="Cantidad de minutos de tolerancia antes de marcar una asistencia como 'llegada tarde'."
    )