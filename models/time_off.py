# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class HrLeave(models.Model):
    _inherit = 'hr.leave'

    apply_discount = fields.Selection(
    [('yes', 'Con Descuento'), ('no', 'Sin descuento')],
    string='Descuento'
    )

    tipo_enfermedad = fields.Selection(
        [('ips', 'Con reposo IPS'), ('privado', 'Con reposo privado')],
        string='Tipo de reposo'
    )