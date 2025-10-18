# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from datetime import date
from odoo.tools.sql import SQL
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)
class HrLeaveAllocationReviewWizard(models.TransientModel):
    _name = 'hr.leave.allocation.review.wizard'
    _description = 'Review Employee Start Dates and Generate Allocations'

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True
    )
    allocation_line_ids = fields.One2many(
        'hr.leave.allocation.review.line', 'wizard_id', string="Employees to Allocate"
    )
    leave_type_id = fields.Many2one(
        "hr.leave.type", string="Time Off Type", required=True,
        domain="[('company_id', 'in', [company_id, False])]")
    

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'allocation_line_ids' in fields_list:
            employees = self.env['hr.employee'].search([('active', '=', True)])
            lines = []
            for emp in employees:
                # Calcular días según política (ej: 15 días/año trabajado, prorrateo si <1 año)
                days = self._compute_allocation_days(emp)
                # Verificar si ya tiene asignación en el año actual
                has_allocation = self._has_allocation_in_year(emp, res.get('year', date.today().year))
                lines.append((0, 0, {
                    'employee_id': emp.id,
                    'date_start': emp.first_contract_date or emp.create_date.date(),
                    'computed_days': days,
                    'has_allocation': has_allocation,
                    'selected': not has_allocation  # Seleccionar solo si no tiene asignación
                }))
            res['allocation_line_ids'] = lines
        return res

    def _compute_allocation_days(self, employee):
        """Calcula los días de vacaciones según la fecha de inicio.
        Ejemplo: 15 días por año completo, prorrateo mensual."""
        start_date = employee.first_contract_date or employee.create_date.date()
        today = date.today()
        if start_date > today:
            return 0.0

        # Años completos
        years = relativedelta(today, start_date).years
        months = relativedelta(today, start_date).months

        # Política: 15 días por año, +1.25 por mes adicional (15/12)
        base_days = 15.0
        total_days = years * base_days + (months * base_days / 12)

        # Opcional: máximo acumulable, tope, etc.
        return round(total_days, 2)

    def _has_allocation_in_year(self, employee, year):
        """Verifica si el empleado ya tiene una asignación de vacaciones en el año dado."""
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        allocations = self.env['hr.leave.allocation'].search([
            ('employee_id', '=', employee.id),
            ('holiday_status_id', '=', self.leave_type_id.id),
            ('state', 'in', ['confirm', 'validate', 'validate1']),
            '|',
                '&', ('date_from', '>=', start), ('date_from', '<=', end),
                '&', ('date_to', '>=', start), ('date_to', '<=', end),
        ])
        return bool(allocations)

    def action_generate_allocations(self):
        self.ensure_one()
        selected_lines = self.allocation_line_ids.filtered(lambda l: l.selected and not l.has_allocation)
        if not selected_lines:
            return {'type': 'ir.actions.act_window_close'}
        _logger.info(selected_lines)
        allocations_vals = []
        for line in selected_lines:
            allocations_vals.append({
                'name': f"Asignación automática {self.year} - {line.employee_id.name}",
                'employee_id': line.employee_id.id,
                'holiday_status_id': self.leave_type_id.id,
                'number_of_days': line.computed_days,
                'allocation_type': 'regular',
                'date_from': date(self.year, 1, 1),
                'date_to': date(self.year, 12, 31),
                'state': 'confirm',
            })

        allocations = self.env['hr.leave.allocation'].create(allocations_vals)
        # Validar automáticamente si no requiere aprobación
        allocations.filtered(lambda a: a.validation_type != 'no_validation').action_validate()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Generated Allocations'),
            'view_mode': 'list,form',
            'res_model': 'hr.leave.allocation',
            'domain': [('id', 'in', allocations.ids)],
        }


class HrLeaveAllocationReviewLine(models.TransientModel):
    _name = 'hr.leave.allocation.review.line'
    _description = 'Allocation Review Line'

    wizard_id = fields.Many2one('hr.leave.allocation.review.wizard', required=True)
    employee_id = fields.Many2one('hr.employee', required=True)
    date_start = fields.Date("Start Date")
    computed_days = fields.Float("Computed Days", digits=(16, 2))
    has_allocation = fields.Boolean("Already Allocated", readonly=True)
    selected = fields.Boolean("Select")

class HrLeaveAllocationReport(models.Model):
    _name = "hr.leave.allocation.report"
    _description = "Time Off Allocation Report"
    _auto = False
    _order = 'employee_id'

    employee_id = fields.Many2one('hr.employee', 'Empleado', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    date_start = fields.Date('Fecha de inicio', readonly=True)
    computed_days = fields.Float('Dias disponibles', readonly=True, digits=(16, 2))
    has_allocation = fields.Boolean('Asignado', compute='_compute_has_allocation')
    year = fields.Integer('Año', readonly=True)
    year = fields.Integer(default=lambda self: date.today().year, required=True)
    liquidation_date = fields.Date('Fecha de liquidación', compute='_compute_allocation_data', store=False)
    available_to_liquidate = fields.Float('Disponible para liquidar', compute='_compute_allocation_data', store=False)
    requires_liquidation = fields.Boolean('Requiere liquidación', compute='_compute_allocation_data', store=False)

    @api.depends('has_allocation')
    def _compute_allocation_data(self):
        for record in self:
            emp = record.employee_id
            if not emp:
                record.update({
                    'has_allocation': False,
                    'liquidation_date': False,
                    'available_to_liquidate': 0.0,
                    'requires_liquidation': False,
                })
                continue
    
            # Calcular el período laboral actual del empleado
            start = emp.x_studio_inicio_neo or emp.first_contract_date or emp.create_date.date()
            today = fields.Date.today()
            years_worked = relativedelta(today, start).years
            period_start = start + relativedelta(years=years_worked)
            period_end = period_start + relativedelta(years=1) - relativedelta(days=1)
    
            # Buscar la asignación EXACTA para este período
            allocation = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['confirm', 'validate', 'validate1']),
                ('date_from', '=', period_start),
                ('date_to', '=', period_end),
            ], limit=1)
    
            if allocation:
                record.update({
                    'has_allocation': True,
                    'liquidation_date': allocation.liquidation_date,
                    'available_to_liquidate': allocation.available_to_liquidate,
                    'requires_liquidation': allocation.requires_liquidation,
                })
                
            else:
                record.update({
                    'has_allocation': False,
                    'liquidation_date': False,
                    'available_to_liquidate': 0.0,
                    'requires_liquidation': False,
                })
                

    def _compute_has_allocation(self):
        for record in self:
            emp = record.employee_id
            start = emp.x_studio_inicio_neo or emp.first_contract_date or emp.create_date.date()
            # Calcular el aniversario más reciente (inicio del período laboral actual)
            today = fields.Date.today()
            years_worked = relativedelta(today, start).years
            period_start = start + relativedelta(years=years_worked)
            period_end = period_start + relativedelta(years=1) - relativedelta(days=1)

            # Buscar asignación para este período exacto
            allocation = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['confirm', 'validate', 'validate1']),
                ('date_from', '=', period_start),
                ('date_to', '=', period_end),
            ], limit=1)

            record.has_allocation = bool(allocation)
    
    @property
    def _table_query(self):
        return SQL("%s %s %s", self._select(), self._from(), self._where())

    def _select(self):
        current_year = date.today().year
        return SQL(
            """
            SELECT
                e.id AS id,
                e.id AS employee_id,
                e.company_id AS company_id,
                COALESCE(e.x_studio_inicio_neo, e.first_contract_date, e.create_date::date) AS date_start,
                CASE
                    WHEN EXTRACT(YEARS FROM AGE(CURRENT_DATE, COALESCE(e.x_studio_inicio_neo, e.first_contract_date, e.create_date::date))) >= 10 THEN 30
                    WHEN EXTRACT(YEARS FROM AGE(CURRENT_DATE, COALESCE(e.x_studio_inicio_neo, e.first_contract_date, e.create_date::date))) >= 6 THEN 18
                    WHEN EXTRACT(YEARS FROM AGE(CURRENT_DATE, COALESCE(e.x_studio_inicio_neo, e.first_contract_date, e.create_date::date))) >= 1 THEN 12
                    ELSE 0
                END AS computed_days,
                %(year)s AS year
            """,
            year=current_year
        )
    
    def _from(self):
        return SQL("FROM hr_employee e")
    
    def _where(self):
        return SQL("WHERE e.active = true")


    def action_generate_allocations2(self):
        if not self:
            raise UserError(_("No hay registros seleccionados."))
    
        # Filtrar solo empleados con días > 0 y sin asignación
        records_to_generate = self.filtered(lambda r: r.computed_days > 0 and not r.has_allocation)
        if not records_to_generate:
            raise UserError(_("No se encontraron asignaciones pendientes (solo se generan si hay días > 0)."))
    
        allocations_vals = []
        for r in records_to_generate:
            emp = r.employee_id
            # 🔁 Calcular fechas directamente (sin método auxiliar)
            start = emp.x_studio_inicio_neo or emp.first_contract_date or emp.create_date.date()
            today = fields.Date.today()
            years_worked = relativedelta(today, start).years
            period_start = start + relativedelta(years=years_worked)
            period_end = period_start + relativedelta(years=1) - relativedelta(days=1)
    
            # Buscar tipo de ausencia de vacaciones
            leave_type = self.env.ref('hr_holidays.holiday_status_cl', raise_if_not_found=False)
            if not leave_type:
                leave_type = self.env['hr.leave.type'].search([('requires_allocation', '!=', 'no')], limit=1)
            if not leave_type:
                raise UserError(_("No se encontró un tipo de ausencia válido para asignaciones."))
    
            allocations_vals.append({
                'name': f"Asignación automática {period_start.year} - {emp.name}",
                'employee_id': emp.id,
                'holiday_status_id': leave_type.id,
                'number_of_days': r.computed_days,
                'allocation_type': 'regular',
                'date_from': period_start,
                'date_to': period_end,
                'state': 'confirm',
            })
    
        if not allocations_vals:
            raise UserError(_("No se generaron asignaciones."))
    
        allocations = self.env['hr.leave.allocation'].create(allocations_vals)
        to_validate = allocations.filtered(lambda a: a.validation_type != 'no_validation')
        if to_validate:
            to_validate.action_validate()
    
        return {
            'type': 'ir.actions.act_window',
            'name': _('Generated Allocations'),
            'res_model': 'hr.leave.allocation',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', allocations.ids)],
        }

    @api.model
    def _cron_generate_missing_allocations(self):
        # Crear un "falso" recordset del reporte ejecutando su consulta
        report_model = self.env['hr.leave.allocation.report']
        self.env.cr.execute(report_model._table_query)
        ids = [r[0] for r in self.env.cr.fetchall()]
        report_records = report_model.browse(ids)
    
        pending = report_records.filtered(lambda r: r.computed_days > 0 and not r.has_allocation)
        if pending:
            _logger.info("Generando %d asignaciones pendientes vía CRON...", len(pending))
            # Llamar al método de generación (que ahora no usa _get_period_dates)
            pending.action_generate_allocations2()