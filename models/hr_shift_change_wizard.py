from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrLeaveShiftChangeWizard(models.TransientModel):
    _name = 'hr.leave.shift.change.wizard'
    _description = 'Aprobación Cambio de Horario'

    leave_id = fields.Many2one('hr.leave', required=True)

    employee_id = fields.Many2one(
        related='leave_id.employee_id',
        string='Empleado',
        readonly=True
    )

    date_from = fields.Datetime(
        related='leave_id.date_from',
        string='Desde',
        readonly=True
    )

    date_to = fields.Datetime(
        related='leave_id.date_to',
        string='Hasta',
        readonly=True
    )

    # Editables
    calendar_days = fields.Many2one(
        'resource.calendar',
        string='Horario aprobado',
        required=True
    )

    shift_start = fields.Float("Hora entrada")
    shift_end = fields.Float("Hora salida")

    def action_confirm(self):
        self.ensure_one()

        leave = self.leave_id

        if not self.calendar_days:
            raise UserError("Debe seleccionar un horario.")

        # Guardamos lo aprobado en el leave
        leave.write({
            'calendar_days': self.calendar_days.id,
            'shift_start': self.shift_start,
            'shift_end': self.shift_end,
        })

        # Validamos el permiso
        leave.action_validate()

        return {'type': 'ir.actions.act_window_close'}