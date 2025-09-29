# -*- coding: utf-8 -*-
from odoo import models, fields, api
from collections import defaultdict
from datetime import datetime
import pytz
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
            lambda c: c.work_entry_source == 'attendance'
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

        # === 3. Aplicar RETRASOS CONFIRMADOS ===
        late_type = self.env['hr.work.entry.type'].search([('code', '=', 'LATE')], limit=1)
        if late_type:
            # Buscar asistencias con retraso confirmado > 0 en el rango
            late_attendances = self.env['hr.attendance'].sudo().search([
                ('employee_id', 'in', self.employee_id.ids),
                ('check_in', '>=', date_from),
                ('check_out', '<=', date_to),
                ('confirmed_late_minutes', '>', 0),
            ])

            total_late_minutes = sum(att.confirmed_late_minutes for att in late_attendances)
            total_late_hours = total_late_minutes / 60.0  # convertir a horas

            work_data[late_type.id] = work_data.get(late_type.id, 0) + total_late_hours
            _logger.info("Retrasos confirmados procesados: %.2f horas", total_late_hours)
                
        else:
            _logger.warning("No se encontró work entry type con código 'LATE_CONFIRMED' para retrasos confirmados.")
        # === Log final ===
        _logger.info(
            "Horas procesadas - Extra Diurna: %.2f, Extra Nocturna: %.2f, "
            "Guardia Diurna: %.2f, Guardia Nocturna: %.2f",
            overtime_day_hours, overtime_night_hours, guard_day_hours, guard_night_hours
        )



    def _get_work_hours(self, date_from, date_to, domain=None):
        """
        Sobreescribe el cálculo de horas para ausencias (is_leave): ahora se calculan
        por días calendario completos (incluyendo sábados y domingos), no por días laborables.
        """
        assert isinstance(date_from, datetime)
        assert isinstance(date_to, datetime)

        _logger.info("Get work hours")
        tzs = set((self.resource_calendar_id or self.employee_id.resource_calendar_id).mapped('tz'))
        assert len(tzs) == 1
        contract_tz_name = tzs.pop()
        tz = pytz.timezone(contract_tz_name) if contract_tz_name else pytz.utc
        utc = pytz.timezone('UTC')
        date_from_tz = tz.localize(date_from).astimezone(utc).replace(tzinfo=None)
        date_to_tz = tz.localize(date_to).astimezone(utc).replace(tzinfo=None)
        work_domain = self._get_work_hours_domain(date_from_tz, date_to_tz, domain=domain, inside=True)
        # Excluir ausencias
        work_domain += [('work_entry_type_id.is_leave', '=', False)]
        # First, found work entries that didn't exceed interval.
        work_entries = self.env['hr.work.entry']._read_group(
            work_domain,
            ['work_entry_type_id'],
            ['duration:sum']
        )
        work_data = defaultdict(int)
        work_data.update({work_entry_type.id: duration_sum for work_entry_type, duration_sum in work_entries})
        self._preprocess_work_hours_data(work_data, date_from, date_to)

        leave_types = self.env['hr.work.entry.type'].search([('is_leave', '=', True)])
        leave_type_ids = leave_types.ids
    
        if leave_type_ids:
            # Buscar todas las ausencias validadas que se solapen con el periodo
            leaves = self.env['hr.leave'].sudo().search([
                ('employee_id', 'in', self.employee_id.ids),
                ('state', '=', 'validate'),  # Solo ausencias aprobadas
                ('date_from', '<=', date_to_tz),
                ('date_to', '>=', date_from_tz),
            ])
            _logger.info("Encontradas %d ausencias en el rango", len(leaves))
            for leave in leaves:
                # Calcular intersección entre el rango solicitado y la ausencia
                leave_start = max(leave.date_from, date_from_tz)
                leave_end = min(leave.date_to, date_to_tz)
    
                if leave_end <= leave_start:
                    continue  # No hay solapamiento real
    
                # Convertir a timezone del contrato para cálculo en fecha local
                leave_start_local = utc.localize(leave_start).astimezone(tz).replace(tzinfo=None)
                leave_end_local = utc.localize(leave_end).astimezone(tz).replace(tzinfo=None)
    
                # Calcular días calendario completos
                delta = leave_end_local - leave_start_local
                total_days = delta.days
                if delta.seconds > 0:
                    total_days += 1  # Incluir día parcial como completo
    
                hours_per_day = 8.0  # Ajusta según tu política
                total_hours = total_days * hours_per_day
    
                # Asignar al tipo de work entry correspondiente
                work_entry_type_id = leave.holiday_status_id.work_entry_type_id.id
                if work_entry_type_id:
                    work_data[work_entry_type_id] += total_hours
                    _logger.info("Ausencia %s: %d días → %.2f horas asignadas a tipo %s",
                        leave.name, total_days, total_hours, leave.holiday_status_id.name)
                else:
                    _logger.warning("Ausencia %s no tiene work_entry_type_id configurado", leave.name)
        # Second, find work entries that exceed interval and compute right duration.
        work_entries = self.env['hr.work.entry'].search(self._get_work_hours_domain(date_from_tz, date_to_tz, domain=domain, inside=False))
        _logger.info(work_entries)
        for work_entry in work_entries:
            local_date_start = utc.localize(work_entry.date_start).astimezone(tz).replace(tzinfo=None)
            local_date_stop = utc.localize(work_entry.date_stop).astimezone(tz).replace(tzinfo=None)
            date_start = max(date_from, local_date_start)
            date_stop = min(date_to, local_date_stop)

            if work_entry.work_entry_type_id.is_leave:
                # ✅ MODIFICACIÓN: Calcular por días CALENDARIO, no laborables
                _logger.info("Modificando día")
                delta = date_stop - date_start
                total_days = delta.days
                if delta.seconds > 0:
                    total_days += 1  # Incluir día parcial como día completo

                # Puedes ajustar 8.0 según la política de tu empresa (ej. 8h/día)
                hours_per_leave_day = 8.0
                work_data[work_entry.work_entry_type_id.id] += total_days * hours_per_leave_day

            else:
                # Para trabajo normal, usar cálculo original
                work_data[work_entry.work_entry_type_id.id] += work_entry._get_work_duration(date_start, date_stop)
        
        return work_data