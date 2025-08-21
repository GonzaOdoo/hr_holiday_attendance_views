from odoo import api, fields, models, tools

from odoo.addons.base.models.res_partner import _tz_get

class HrLateForm(models.Model):
    _name = "hr.leave.late.form"

    attendance_id = fields.Many2one('hr.attendance',string='Asistencias')
    date_start = fields.Datetime('Fecha de ingreso',related='attendance_id.check_in')
    date_end = fields.Datetime('Fecha y hora de salida')
    note = fields.Text('Justificacion')
    state = fields.Selection([('pending','Pendiente'),('accepted','Aceptado')])
    employee_id = fields.Many2one('hr.employee','Empleado')
    