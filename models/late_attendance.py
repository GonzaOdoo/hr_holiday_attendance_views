# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class HrContract(models.Model):
    _inherit = 'hr.contract'

    def _preprocess_work_hours_data(self, work_data, date_from, date_to):
        """
        Extiende el método para soportar:
        - Horas extra diurnas/nocturnas (OVERTIME_EVENING / OVERTIME_NIGHT)
        - Guardias diurnas/nocturnas (GUARD_EVENING / GUARD_NIGHT)
        Las guardias se marcan con `is_guard = True` en hr.attendance y se ignoran en horas extra.
        """
        # Filtrar contratos relevantes
        attendance_contracts = self.filtered(
            lambda c: c.work_entry_source == 'attendance' and c.wage_type == 'hourly'
        )
        if not attendance_contracts:
            return

        # Tipo de entrada por defecto (horas normales)
        default_work_entry_type = self.structure_type_id.default_work_entry_type_id
        if len(default_work_entry_type) != 1:
            return

        # === Tipos de entrada para HORAS EXTRA ===
        overtime_normal_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME')], limit=1)
        overtime_day_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME_EVENING')], limit=1)
        overtime_night_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME_NIGHT')], limit=1)

        # === Tipos de entrada para GUARDIAS ===
        guard_day_type = self.env['hr.work.entry.type'].search([('code', '=', 'GUARD_EVENING')], limit=1)
        guard_night_type = self.env['hr.work.entry.type'].search([('code', '=', 'GUARD_NIGHT')], limit=1)

        # Validar que existan los tipos necesarios
        if not overtime_day_type or not overtime_night_type:
            _logger.warning("No se encontraron work entry types para OVERTIME_EVENING o OVERTIME_NIGHT")
        if not guard_day_type or not guard_night_type:
            _logger.warning("No se encontraron work entry types para GUARD_EVENING o GUARD_NIGHT")
            # Puedes return si las guardias son obligatorias
            # return

        # Rangos horarios
        NIGHT_START = 22  # 10 PM
        NIGHT_END = 6     # 6 AM

        # === Buscar asistencias en el rango ===
        attendances = self.env['hr.attendance'].sudo().search([
            ('employee_id', 'in', self.employee_id.ids),
            ('check_in', '>=', date_from),
            ('check_out', '<=', date_to),
        ])

        # Acumuladores
        total_overtime = 0.0
        total_guards = 0.0

        overtime_day_hours = 0.0
        overtime_night_hours = 0.0

        guard_day_hours = 0.0
        guard_night_hours = 0.0

        for att in attendances:
            # === CASO 1: Es una GUARDIA ===
            if att.is_guard:
                # Usar las horas totales trabajadas (no solo overtime)
                hours = att.worked_hours
                if hours <= 0:
                    continue

                hour_in = att.check_in.hour
                if hour_in >= NIGHT_START or hour_in < NIGHT_END:
                    guard_night_hours += hours
                else:
                    guard_day_hours += hours
                total_guards += hours

            # === CASO 2: Es HORA EXTRA (y NO es guardia) ===
            elif not att.is_guard and att.overtime_hours > 0 and att.overtime_status == 'approved':
                hour_in = att.check_in.hour
                if hour_in >= NIGHT_START or hour_in < NIGHT_END:
                    overtime_night_hours += att.overtime_hours
                else:
                    overtime_day_hours += att.overtime_hours
                total_overtime += att.overtime_hours

        # === 1. Aplicar HORAS EXTRA ===
        if total_overtime > 0 and default_work_entry_type.id in work_data:
            work_data[default_work_entry_type.id] -= total_overtime

        if overtime_day_hours > 0 and overtime_day_type:
            work_data[overtime_day_type.id] = work_data.get(overtime_day_type.id, 0) + overtime_day_hours

        if overtime_night_hours > 0 and overtime_night_type:
            work_data[overtime_night_type.id] = work_data.get(overtime_night_type.id, 0) + overtime_night_hours

        # === 2. Aplicar GUARDIAS ===
        if total_guards > 0 and default_work_entry_type.id in work_data:
            # Restar también las guardias del tiempo normal (si se marcan como trabajo)
            # Opcional: si las guardias NO deben restar de horas normales, comenta esta línea
            work_data[default_work_entry_type.id] -= total_guards

        if guard_day_hours > 0 and guard_day_type:
            work_data[guard_day_type.id] = work_data.get(guard_day_type.id, 0) + guard_day_hours

        if guard_night_hours > 0 and guard_night_type:
            work_data[guard_night_type.id] = work_data.get(guard_night_type.id, 0) + guard_night_hours

        # === Log final ===
        _logger.info(
            "Horas procesadas - Extra Diurna: %.2f, Extra Nocturna: %.2f, "
            "Guardia Diurna: %.2f, Guardia Nocturna: %.2f",
            overtime_day_hours, overtime_night_hours, guard_day_hours, guard_night_hours
        )