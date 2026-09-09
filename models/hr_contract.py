# -*- coding: utf-8 -*-
from odoo import models, fields, api
from collections import defaultdict
from datetime import datetime,timedelta,time
import pytz
from pytz import timezone
import logging

_logger = logging.getLogger(__name__)

class HrContract(models.Model):
    _inherit = 'hr.contract'
    hourly_rate = fields.Monetary(string="Por hora",compute='_compute_hourly_rate')

    def _compute_hourly_rate(self):
        for record in self:
            if record.schedule_pay == 'monthly':
                record.hourly_rate = record.wage / 30 / 8
            else:
                 record.hourly_rate = 0

    def _preprocess_work_hours_data(self, work_data, date_from, date_to):
        """
        Extiende el cálculo de horas de trabajo para nómina.
    
        Procesa:
            - Horas extra diurnas: OVERTIME_EVENING
            - Horas extra nocturnas: OVERTIME_NIGHT
            - Guardias diurnas: GUARD_EVENING
            - Guardias nocturnas: GUARD_NIGHT
            - Retrasos confirmados: LATE
            - Recargo nocturno: RECARGON
    
        Las horas extra se toman directamente de:
            hr.attendance.overtime_day
            hr.attendance.overtime_night
    
        Solamente se consideran horas extra con:
            overtime_status == 'approved'
    
        Las guardias se identifican mediante:
            is_guard == True
    
        Las guardias no se consideran horas extra.
        """
    
        # ============================================================
        # 1. CONTRATOS RELEVANTES
        # ============================================================
    
        attendance_contracts = self.filtered(
            lambda c: c.work_entry_source == 'attendance'
        )
    
        if not attendance_contracts:
            return
    
        # Tipo de entrada normal
        default_work_entry_type = self.structure_type_id.default_work_entry_type_id
    
        if len(default_work_entry_type) != 1:
            return
    
        # ============================================================
        # 2. TIPOS DE WORK ENTRY
        # ============================================================
    
        WorkEntryType = self.env['hr.work.entry.type']
    
        overtime_day_type = WorkEntryType.search(
            [('code', '=', 'OVERTIME_EVENING')],
            limit=1
        )
    
        overtime_night_type = WorkEntryType.search(
            [('code', '=', 'OVERTIME_NIGHT')],
            limit=1
        )
    
        guard_day_type = WorkEntryType.search(
            [('code', '=', 'GUARD_EVENING')],
            limit=1
        )
    
        guard_night_type = WorkEntryType.search(
            [('code', '=', 'GUARD_NIGHT')],
            limit=1
        )
    
        late_type = WorkEntryType.search(
            [('code', '=', 'LATE')],
            limit=1
        )
    
        recargo_nocturno_type = WorkEntryType.search(
            [('code', '=', 'RECARGON')],
            limit=1
        )
    
        # ============================================================
        # 3. ADVERTENCIAS
        # ============================================================
    
        if not overtime_day_type or not overtime_night_type:
            _logger.warning(
                "No se encontraron los work entry types "
                "OVERTIME_EVENING / OVERTIME_NIGHT"
            )
    
        if not guard_day_type or not guard_night_type:
            _logger.warning(
                "No se encontraron los work entry types "
                "GUARD_EVENING / GUARD_NIGHT"
            )
    
        if not late_type:
            _logger.warning(
                "No se encontró work entry type con código 'LATE'"
            )
    
        if not recargo_nocturno_type:
            _logger.warning(
                "No se encontró work entry type con código 'RECARGON'"
            )
    
        # ============================================================
        # 4. BUSCAR ASISTENCIAS
        # ============================================================
    
        attendances = self.env['hr.attendance'].sudo().search([
            ('employee_id', 'in', self.employee_id.ids),
            ('check_in_date', '>=', date_from.date()),
            ('check_in_date', '<=', date_to.date()),
        ])
    
        _logger.info(
            "Asistencias encontradas para nómina: %s",
            len(attendances)
        )
    
        # ============================================================
        # 5. ACUMULADORES
        # ============================================================
    
        total_overtime = 0.0
    
        overtime_day_hours = 0.0
        overtime_night_hours = 0.0
    
        total_guards = 0.0
    
        guard_day_hours = 0.0
        guard_night_hours = 0.0
    
        # ============================================================
        # 6. PROCESAR ASISTENCIAS
        # ============================================================
    
        for att in attendances:
    
            _logger.info(
                "Asistencia %s - %s / %s | Guardia=%s | "
                "Estado HE=%s | HED=%.2f | HEN=%.2f",
                att.employee_id.name,
                att.check_in,
                att.check_out,
                att.is_guard,
                att.overtime_status,
                att.overtime_day or 0.0,
                att.overtime_night or 0.0,
            )
    
            # --------------------------------------------------------
            # CASO 1: GUARDIA
            # --------------------------------------------------------
    
            if att.is_guard:
    
                hours = att.worked_hours or 0.0
    
                if hours <= 0:
                    continue
    
                # Para mantener la lógica actual:
                # la guardia completa se clasifica según la hora
                # de entrada.
    
                hour_in = att.check_in.hour if att.check_in else 0
    
                NIGHT_START = 20
                NIGHT_END = 6
    
                if hour_in >= NIGHT_START or hour_in < NIGHT_END:
                    guard_night_hours += hours
                else:
                    guard_day_hours += hours
    
                total_guards += hours
    
                continue
    
            # --------------------------------------------------------
            # CASO 2: HORAS EXTRA
            #
            # IMPORTANTE:
            # Solamente llevar a nómina las horas APROBADAS.
            #
            # Ya no recalculamos el intervalo ni las horas nocturnas.
            # Usamos directamente los campos de hr.attendance.
            # --------------------------------------------------------
    
            if att.overtime_status == 'approved':
    
                day_overtime = att.overtime_day or 0.0
                night_overtime = att.overtime_night or 0.0
    
                if day_overtime > 0:
                    overtime_day_hours += day_overtime
    
                if night_overtime > 0:
                    overtime_night_hours += night_overtime
    
                total_overtime += (
                    day_overtime +
                    night_overtime
                )
    
                _logger.info(
                    "HE aprobadas - %s: HED=%.2f HEN=%.2f Total=%.2f",
                    att.employee_id.name,
                    day_overtime,
                    night_overtime,
                    day_overtime + night_overtime,
                )
    
        # ============================================================
        # 7. APLICAR HORAS EXTRA
        # ============================================================
    
        _logger.info(
            "Total horas extra aprobadas: %.2f",
            total_overtime
        )
    
        _logger.info(
            "Horas extra diurnas aprobadas: %.2f",
            overtime_day_hours
        )
    
        _logger.info(
            "Horas extra nocturnas aprobadas: %.2f",
            overtime_night_hours
        )
    
        # Restar las horas extra del trabajo normal
        if (
            total_overtime > 0
            and default_work_entry_type.id in work_data
        ):
            work_data[default_work_entry_type.id] -= total_overtime
    
        # HED
        if overtime_day_hours > 0 and overtime_day_type:
            work_data[overtime_day_type.id] = (
                work_data.get(overtime_day_type.id, 0.0)
                + overtime_day_hours
            )
    
        # HEN
        if overtime_night_hours > 0 and overtime_night_type:
            work_data[overtime_night_type.id] = (
                work_data.get(overtime_night_type.id, 0.0)
                + overtime_night_hours
            )
    
        # ============================================================
        # 8. APLICAR GUARDIAS
        # ============================================================
    
        _logger.info(
            "Guardias - Diurnas: %.2f | Nocturnas: %.2f | Total: %.2f",
            guard_day_hours,
            guard_night_hours,
            total_guards,
        )
    
        # Restar guardias de las horas normales
        if (
            total_guards > 0
            and default_work_entry_type.id in work_data
        ):
            work_data[default_work_entry_type.id] -= total_guards
    
        # Guardia diurna
        if guard_day_hours > 0 and guard_day_type:
            work_data[guard_day_type.id] = (
                work_data.get(guard_day_type.id, 0.0)
                + guard_day_hours
            )
    
        # Guardia nocturna
        if guard_night_hours > 0 and guard_night_type:
            work_data[guard_night_type.id] = (
                work_data.get(guard_night_type.id, 0.0)
                + guard_night_hours
            )
    
        # ============================================================
        # 9. RETRASOS CONFIRMADOS
        # ============================================================
    
        if late_type:
    
            late_attendances = self.env['hr.attendance'].sudo().search([
                ('employee_id', 'in', self.employee_id.ids),
                ('check_in', '>=', date_from),
                ('check_out', '<=', date_to),
                ('confirmed_late_minutes', '>', 0),
            ])
    
            total_late_minutes = sum(
                att.confirmed_late_minutes
                for att in late_attendances
            )
    
            total_late_hours = total_late_minutes / 60.0
    
            if total_late_hours > 0:
    
                work_data[late_type.id] = (
                    work_data.get(late_type.id, 0.0)
                    + total_late_hours
                )
    
                _logger.info(
                    "Retrasos confirmados procesados: %.2f horas",
                    total_late_hours
                )
    
        # ============================================================
        # 10. RECARGO NOCTURNO
        # ============================================================
    
        if recargo_nocturno_type:
    
            _logger.info(
                "Inicio cálculo recargo nocturno"
            )
    
            total_recargo_nocturno = 0.0
    
            for att in attendances:
    
                if not att.check_in or not att.check_out:
                    continue
    
                total_att_hours = (
                    att.check_out - att.check_in
                ).total_seconds() / 3600.0
    
                if total_att_hours <= 0:
                    continue
    
                night_hours = att.night_hours or 0.0
    
                total_recargo_nocturno += night_hours
    
            if total_recargo_nocturno > 0:
    
                work_data[recargo_nocturno_type.id] = (
                    work_data.get(
                        recargo_nocturno_type.id,
                        0.0
                    )
                    + total_recargo_nocturno
                )
    
                _logger.info(
                    "Recargo nocturno procesado: %.2f horas",
                    total_recargo_nocturno
                )
    
        # ============================================================
        # 11. LOG FINAL
        # ============================================================
    
        _logger.info(
            "Horas procesadas - "
            "Extra Diurna: %.2f, "
            "Extra Nocturna: %.2f, "
            "Guardia Diurna: %.2f, "
            "Guardia Nocturna: %.2f, "
            "Recargo Nocturno: %.2f",
            overtime_day_hours,
            overtime_night_hours,
            guard_day_hours,
            guard_night_hours,
            total_recargo_nocturno if recargo_nocturno_type else 0.0,
        )

    def _get_work_hours(self, date_from, date_to, domain=None):
        """
        Sobreescribe el cálculo de horas para ausencias (is_leave): ahora se calculan
        por días calendario completos (incluyendo sábados y domingos), no por días laborables.
        """
        assert isinstance(date_from, datetime)
        assert isinstance(date_to, datetime)

        _logger.info("Get work hours")
        tzs = set((self.resource_calendar_id or self.employee_id.resource_calendar_id or self.company_id.resource_calendar_id).mapped('tz'))
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


    def _get_night_hours_between(self, start, end, night_start=20, night_end=6, tz_name='America/Asuncion'):
        if start >= end:
            return 0.0
    
        tz = timezone(tz_name)
    
        # Convertir a hora local
        start_local = start.astimezone(tz)
        end_local = end.astimezone(tz)
    
        total = 0.0
        current_day = start_local.date()
    
        while datetime.combine(current_day, time(0, 0), tz) < end_local:
            night_start_dt = tz.localize(datetime.combine(current_day, time(night_start, 0)))
            midnight = tz.localize(datetime.combine(current_day + timedelta(days=1), time(0, 0)))
            night_end_dt = tz.localize(datetime.combine(current_day + timedelta(days=1), time(night_end, 0)))
    
            # 20:00 → 24:00
            s = max(start_local, night_start_dt)
            e = min(end_local, midnight)
            if e > s:
                total += (e - s).total_seconds() / 3600
    
            # 00:00 → 06:00
            s = max(start_local, midnight)
            e = min(end_local, night_end_dt)
            if e > s:
                total += (e - s).total_seconds() / 3600
    
            current_day += timedelta(days=1)
    
        return total