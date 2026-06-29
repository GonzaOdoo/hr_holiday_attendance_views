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
        allocations.filtered(lambda a: a.validation_type != 'no_validation').action_approve()
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
    _description = "Estado de liquidaciones"
    _auto = False
    _order = 'employee_id'

    employee_id = fields.Many2one('hr.employee', 'Empleado', readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    date_start = fields.Date('Fecha de inicio', readonly=True)
    computed_days = fields.Float('Año actual (Días disponibles segun ley)', readonly=True, digits=(16, 2))
    days_taken = fields.Float('Días tomados', compute='_compute_allocation_data', store=False, digits=(16, 2))
    carryover_days = fields.Float('Saldo años anteriores', compute='_compute_allocation_data', store=False, digits=(16, 2))
    total_available = fields.Float('Total disponible', compute='_compute_allocation_data', store=False, digits=(16, 2))
    remaining_days = fields.Float('Saldo', compute='_compute_remaining_days')
    has_allocation = fields.Boolean('Asignado', compute='_compute_has_allocation')
    allocation_id = fields.Many2one('hr.leave.allocation',string='Asignación',compute='_compute_has_allocation')
    year = fields.Integer('Año', readonly=True)
    year = fields.Integer(default=lambda self: date.today().year, required=True)
    liquidation_date = fields.Date('Fecha de liquidación', compute='_compute_allocation_data', store=False)
    available_to_liquidate = fields.Float('Disponible para liquidar', compute='_compute_available_to_liquidate', store=False)
    requires_liquidation = fields.Boolean('Requiere liquidación', compute='_compute_allocation_data', store=False)
    already_liquidation_id = fields.Many2one(
        'hr.leave.liquidation',
        compute='_compute_already_liquidated',
    )
    
    has_liquidation_leave = fields.Boolean(
        compute='_compute_already_liquidated',
        store=False
    )

    @api.depends('allocation_id', 'computed_days')
    def _compute_available_to_liquidate(self):
        for record in self:
            if not record.allocation_id:
                record.available_to_liquidate = 0.0
                continue
    
            total_liquidated = sum(
                self.env['hr.leave.liquidation'].search([
                    ('allocation_id', '=', record.allocation_id.id),
                ]).mapped('days')
            )
    
            record.available_to_liquidate = max(
                0.0,
                record.computed_days - total_liquidated
            )

    @api.depends('total_available','days_taken')
    def _compute_remaining_days(self):
        for record in self:
            record.remaining_days = record.total_available - record.days_taken

    @api.depends('employee_id', 'requires_liquidation')
    def _compute_already_liquidated(self):
        for record in self:
            record.already_liquidation_id = False
            record.has_liquidation_leave = False
    
            if not record.employee_id:
                continue
    
            # 1. Obtener la asignación REAL asociada a este período (igual que en _compute_allocation_data)
            emp = record.employee_id
            start = emp.x_studio_inicio or emp.first_contract_date or emp.create_date.date()
            today = fields.Date.today()
            years_worked = relativedelta(today, start).years
            period_start = start + relativedelta(years=years_worked)
            period_end = period_start + relativedelta(years=1) - relativedelta(days=1)
    
            allocation = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['confirm', 'validate', 'validate1']),
                ('date_from', '=', period_start),
                ('date_to', '=', period_end),
            ], limit=1)
    
            if not allocation:
                continue
    
            # 2. ✅ BUSCAR DIRECTAMENTE POR allocation_id (¡mucho más seguro!)
            liquidation = self.env['hr.leave.liquidation'].search([
                ('allocation_id', '=', allocation.id),
            ], limit=1)
    
            if liquidation:
                record.already_liquidation_id = liquidation
                record.has_liquidation_leave = True

    @api.depends('employee_id')
    def _compute_allocation_data(self):
        for record in self:
            emp = record.employee_id
            if not emp:
                record.update({
                    'has_allocation': False,
                    'days_taken': 0.0,
                    'carryover_days': 0.0,
                    'total_available': 0.0,
                    'liquidation_date': False,
                    'available_to_liquidate': 0.0,
                    'requires_liquidation': False,
                })
                continue
    
            # Calcular período laboral actual
            start = emp.x_studio_inicio or emp.first_contract_date or emp.create_date.date()
            today = fields.Date.today()
            years_worked = relativedelta(today, start).years
            period_start = start + relativedelta(years=years_worked)
            period_end = period_start + relativedelta(years=1) - relativedelta(days=1)
    
            # Buscar asignación ACTUAL para este período
            current_allocation = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['confirm', 'validate', 'validate1']),
                ('date_from', '=', period_start),
                ('date_to', '=', period_end),
            ], limit=1)
    
            # Calcular días tomados (si existe asignación actual)
            days_taken = current_allocation.leaves_taken if current_allocation else 0.0
    
            # === CÁLCULO DE REMANENTE (carry-over) ===
            carryover_days = 0.0
            
            # Buscar asignación del período ANTERIOR (solo si hay años trabajados > 0)
            if years_worked > 0:
                prev_period_start = start + relativedelta(years=years_worked - 1)
                prev_period_end = prev_period_start + relativedelta(years=1) - relativedelta(days=1)
                _logger.info(prev_period_start)
                _logger.info(prev_period_end)
                _logger.info(emp.name)
                prev_allocation = self.env['hr.leave.allocation'].search([
                    ('employee_id', '=', emp.id),
                    ('state', 'in', ['confirm', 'validate', 'validate1']),
                    ('date_from', '=', prev_period_start),
                    ('date_to', '=', prev_period_end),
                ], limit=1)
                _logger.info(prev_allocation)
                if prev_allocation:
                    # Solo acumular si NO requiere liquidación y tiene carryover habilitado
                    remaining_days = max(0.0, prev_allocation.number_of_days - prev_allocation.leaves_taken)
                    # Aplicar límite máximo de carryover si existe
                    carryover_days = remaining_days
    
            # Total disponible para asignar = días legales + remanente
            total_available = record.computed_days + carryover_days
    
            # Datos de liquidación (solo si hay asignación actual)
            if current_allocation:
                record.update({
                    'has_allocation': True,
                    'days_taken': days_taken,
                    'carryover_days': carryover_days,
                    'total_available': total_available,
                    'liquidation_date': current_allocation.liquidation_date,
                    #'available_to_liquidate': current_allocation.available_to_liquidate,
                    'requires_liquidation': current_allocation.requires_liquidation,
                })
            else:
                record.update({
                    'has_allocation': False,
                    'days_taken': 0.0,
                    'carryover_days': carryover_days,  # Mostrar remanente incluso sin asignación actual
                    'total_available': total_available,
                    'liquidation_date': False,
                    'available_to_liquidate': 0.0,
                    'requires_liquidation': False,
                })

                

    def _compute_has_allocation(self):
        for record in self:
            emp = record.employee_id
            start = emp.x_studio_inicio or emp.first_contract_date or emp.create_date.date()
            record.allocation_id = False
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
            if allocation:
                record.allocation_id = allocation
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
                COALESCE(e.x_studio_inicio, e.first_contract_date, e.create_date::date) AS date_start,
                CASE
                    WHEN EXTRACT(YEARS FROM AGE(CURRENT_DATE, COALESCE(e.x_studio_inicio, e.first_contract_date, e.create_date::date))) >= 10 THEN 30
                    WHEN EXTRACT(YEARS FROM AGE(CURRENT_DATE, COALESCE(e.x_studio_inicio, e.first_contract_date, e.create_date::date))) >= 6 THEN 18
                    WHEN EXTRACT(YEARS FROM AGE(CURRENT_DATE, COALESCE(e.x_studio_inicio, e.first_contract_date, e.create_date::date))) >= 1 THEN 12
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
            start = emp.x_studio_inicio or emp.first_contract_date or emp.create_date.date()
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
            existing = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', emp.id),
                ('date_from', '=', period_start),
                ('date_to', '=', period_end),
                ('holiday_status_id', '=', leave_type.id),
            ], limit=1)
            
            if existing:
                continue
            allocations_vals.append({
                'name': f"Asignación automática {period_start.year} - {emp.name}",
                'employee_id': emp.id,
                'holiday_status_id': leave_type.id,
                'number_of_days': r.total_available,
                'allocation_type': 'regular',
                'date_from': period_start,
                'date_to': period_end,
                'state': 'confirm',
            })
    
        if not allocations_vals:
            raise UserError(_("No se generaron asignaciones."))
    
        allocations = self.env['hr.leave.allocation'].create(allocations_vals)
        to_validate = allocations.filtered(lambda a: a.validation_type != 'no_validation')
        _logger.info("Validar!")
        _logger.info(to_validate)
        if to_validate:
            to_validate.action_approve()
            to_validate.action_validate()
    
        return

    @api.model
    def _cron_generate_missing_allocations(self):
        report_records = self.env['hr.leave.allocation.report'].search([])
        _logger.info("Start CRON!!!")
        _logger.info(report_records)
        pending = report_records.filtered(
            lambda r: r.computed_days > 0 and not r.has_allocation
        )
    
        if pending:
            _logger.info(
                "Generando %d asignaciones pendientes vía CRON...",
                len(pending)
            )
    
            pending.action_generate_allocations2()


    def action_liquidate_allocation(self):
        self.ensure_one()
    
        return {
            'type': 'ir.actions.act_window',
            'name': _('Liquidar vacaciones'),
            'res_model': 'hr.leave.liquidation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_report_id': self.id,
                'default_liquidation_date':fields.Date.today(),
            }
        }


    def action_liquidate_selected_allocations(self):
        records_to_liquidate = self.filtered(
            lambda r: r.available_to_liquidate > 0
        )
    
        if not records_to_liquidate:
            raise UserError(_(
                "No hay asignaciones para liquidar."
            ))
    
        return {
            'type': 'ir.actions.act_window',
            'name': _('Liquidación masiva'),
            'res_model': 'hr.leave.liquidation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_report_ids': [(6, 0, records_to_liquidate.ids)],
                'default_liquidation_date': fields.Date.today(),
            }
        }