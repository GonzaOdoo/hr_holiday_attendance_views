# -*- coding: utf-8 -*-
from odoo import models, fields, api
from collections import defaultdict
from datetime import datetime,timedelta
from odoo.addons.resource.models.utils import Intervals
import pytz
from pytz import timezone
import logging

_logger = logging.getLogger(__name__)

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    extra_calendar_id = fields.Many2one('resource.calendar',string='Horas adicionales', check_company=True)
    extra_calendar_start = fields.Datetime(string='Inicio')
    extra_calendar_end = fields.Datetime('Fin')
    shift_change_ids = fields.One2many(
        'hr.employee.shift.change',
        'employee_id',
        string="Cambios de Horario"
    )
   


    def _employee_attendance_intervals(self, start_dt, end_dt, lunch=False):
        self.ensure_one()
    
        intervals = super()._employee_attendance_intervals(start_dt, end_dt, lunch=lunch)
    
        # 🔥 Normalizar a UTC naive (como guarda Odoo)

    
        # Buscar cambios que intersecten el rango
        shift_changes = self.env['hr.employee.shift.change'].search([
            ('employee_id', '=', self.id),
            ('state', '=', 'approved'),
            ('date_start', '<=', end_dt),
            ('date_end', '>=', start_dt),
        ])
    
        if not shift_changes:
            return intervals
    
        for change in shift_changes:
            extra_intervals = change.calendar_id._attendance_intervals_batch(
                start_dt,
                end_dt,
                self.resource_id,
                lunch=lunch
            ).get(self.resource_id.id, Intervals())
    
            intervals |= extra_intervals
    
        return intervals

class HrEmployeeShiftChange(models.Model):
    _name = 'hr.employee.shift.change'
    _description = 'Historial Cambio de Horario'
    _order = 'date_start desc'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade')
    leave_id = fields.Many2one('hr.leave',string='Tiempo personal', ondelete='cascade')

    calendar_id = fields.Many2one('resource.calendar',string='Horario', required=True)
    date_start = fields.Datetime(string='Desde',required=True)
    date_end = fields.Datetime(string='Hasta',required=True)

    state = fields.Selection([
        ('approved', 'Aprobado'),
        ('cancelled', 'Cancelado'),
    ], default='approved')