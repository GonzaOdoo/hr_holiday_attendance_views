
from odoo import api, fields, models, tools

from odoo.addons.base.models.res_partner import _tz_get


class LeaveReportCalendar(models.Model):
    _inherit = "hr.leave.report.calendar"

    source = fields.Selection([
        ('leave', 'Time Off'),
        ('attendance', 'Attendance')
    ], string="Source", readonly=True)

    attendance_id = fields.Many2one('hr.attendance', readonly=True, string="Attendance")
    color = fields.Integer("Color Index", compute="_compute_color", store=False)

    def init(self):
        tools.drop_view_if_exists(self._cr, 'hr_leave_report_calendar')
        self._cr.execute("""
            CREATE OR REPLACE VIEW hr_leave_report_calendar AS
            -- Part 1: Time Off Leaves
            SELECT
                hl.id AS id,
                'leave' AS source,
                hl.id AS leave_id,
                NULL::integer AS attendance_id,
                hl.date_from AS start_datetime,
                hl.date_to AS stop_datetime,
                hl.employee_id AS employee_id,
                hl.state AS state,
                hl.department_id AS department_id,
                hl.number_of_days AS duration,
                hl.private_name AS description,
                hl.holiday_status_id AS holiday_status_id,
                em.company_id AS company_id,
                em.job_id AS job_id,
                COALESCE(rr.tz, co_partner.tz, 'UTC') AS tz,  -- ← Corregido
                hl.state = 'refuse' AS is_striked,
                hl.state NOT IN ('validate', 'refuse') AS is_hatched
            FROM hr_leave hl
            LEFT JOIN hr_employee em ON em.id = hl.employee_id
            LEFT JOIN resource_resource rr ON rr.id = em.resource_id
            LEFT JOIN res_company co ON co.id = em.company_id
            LEFT JOIN res_partner co_partner ON co_partner.id = co.partner_id  -- ← Nuevo JOIN
            WHERE hl.state IN ('confirm', 'validate', 'validate1', 'refuse')
    
            UNION ALL
    
            -- Part 2: Attendances
            SELECT
                -a.id AS id,
                'attendance' AS source,
                NULL::integer AS leave_id,
                a.id AS attendance_id,
                a.check_in AS start_datetime,
                COALESCE(a.check_out, NOW() AT TIME ZONE 'UTC') AS stop_datetime,
                a.employee_id AS employee_id,
                'validate' AS state,
                em.department_id AS department_id,
                CASE
                    WHEN a.check_out IS NOT NULL THEN
                        ROUND(EXTRACT('epoch' FROM (a.check_out - a.check_in)) / 3600.0, 2)
                    ELSE
                        ROUND(EXTRACT('epoch' FROM (NOW() AT TIME ZONE 'UTC' - a.check_in)) / 3600.0, 2)
                END AS duration,
                'Attendance' AS description,
                NULL::integer AS holiday_status_id,
                em.company_id AS company_id,
                em.job_id AS job_id,
                COALESCE(rr.tz, rc.tz, cc.tz, 'UTC') AS tz,
                FALSE AS is_striked,
                FALSE AS is_hatched
            FROM hr_attendance a
            LEFT JOIN hr_employee em ON em.id = a.employee_id
            LEFT JOIN resource_resource rr ON rr.id = em.resource_id
            LEFT JOIN resource_calendar rc ON rc.id = em.resource_calendar_id
            LEFT JOIN res_company co ON co.id = em.company_id
            LEFT JOIN resource_calendar cc ON cc.id = co.resource_calendar_id
            WHERE a.check_in IS NOT NULL
        """)  # ← Nota: sin ; dentro, sin paréntesis extra

    @api.depends('employee_id.name', 'leave_id', 'attendance_id')
    def _compute_name(self):
        for record in self:
            if record.source == 'leave':
                record.name = record.employee_id.name
                if self.env.user.has_group('hr_holidays.group_hr_holidays_user'):
                    record.name = f" {record.leave_id.holiday_status_id.name}"
                record.name += f": {record.sudo().leave_id.duration_display}"
            else:  # attendance
                duration = record.duration if record.duration else 0.0
                record.name = f"{record.employee_id.name}: Asistencia ({duration:.2f}h)"



    @api.depends('source', 'state', 'holiday_status_id', 'duration')
    def _compute_color(self):
        for record in self:
            record.color = 0  # default
            if record.source == 'attendance':
                # Colorear asistencias por estado o duración
                if not record.attendance_id.check_out:
                    record.color = 1  # Rojo: aún no ha salido (en curso)
                else:
                    if record.attendance_id.overtime_status == 'to_approve':
                        record.color = 2
                    else:
                        record.color = 0  # Verde: jornada completa
            elif record.source == 'leave':
                # Colorear ausencias por tipo
                # Asignamos un color por tipo de ausencia (usa el ID o un hash)
                if record.holiday_status_id:
                    # Usa el ID del tipo de ausencia para asignar color (0-7)
                    record.color = (record.holiday_status_id.id % 7) + 1
                else:
                    record.color = 5  # Azul claro: tipo desconocido


    def action_open_record(self):
        self.ensure_one()
        if self.source == 'leave' and self.leave_id:
            return self.leave_id.get_formview_action()
        elif self.source == 'attendance' and self.attendance_id:
            return {
            'type': 'ir.actions.act_window',
            'name': 'Edit Attendance',
            'res_model': 'hr.attendance',
            'res_id': self.attendance_id.id,
            'view_mode': 'form',
            'views': [
                (self.env.ref('hr_attendance_gantt.hr_attendance_gantt_create_view_form').id, 'form')
            ],
            'target': 'new',  # Abre como popup (wizard)
            'context': self._context,
        }

        return {'type': 'ir.actions.act_window_close'}