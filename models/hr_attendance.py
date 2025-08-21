# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class HrContract(models.Model):
    _inherit = 'hr.attendance'

    is_guard = fields.Boolean('Es guardia')
    nocturno = fields.Boolean(
        'Nocturno', 
        compute='_compute_nocturno',
        store=True  # No se guarda en la base de datos
    )

    @api.depends('check_in')
    def _compute_nocturno(self):
        for attendance in self:
            if attendance.check_in:
                # Convertir el datetime a hora local
                check_in_time = fields.Datetime.context_timestamp(
                    attendance, 
                    attendance.check_in
                ).time()
                
                # Verificar si está entre las 22:00 y 6:00
                attendance.nocturno = (
                    check_in_time.hour >= 22 or 
                    check_in_time.hour < 6
                )
            else:
                attendance.nocturno = False