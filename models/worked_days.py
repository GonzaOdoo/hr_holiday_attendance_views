# -*- coding: utf-8 -*-
#Calcula el monto a pagar en las lineas de nomina
from odoo import models,api
import logging

_logger = logging.getLogger(__name__)
class HrPayslipWorkedDays(models.Model):
    _inherit = 'hr.payslip.worked_days'

    @api.depends('number_of_days','number_of_hours')
    def _compute_amount(self):
        # === Tipos de entrada especiales ===
        _logger.info("Compute amount!!!!!")
        overtime_day_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME_EVENING')], limit=1)
        overtime_night_type = self.env['hr.work.entry.type'].search([('code', '=', 'OVERTIME_NIGHT')], limit=1)
        guard_day_type = self.env['hr.work.entry.type'].search([('code', '=', 'GUARD_EVENING')], limit=1)
        guard_night_type = self.env['hr.work.entry.type'].search([('code', '=', 'GUARD_NIGHT')], limit=1)
        recargo_nocturno_type = self.env['hr.work.entry.type'].search([('code', '=', 'RECARGON')], limit=1) 
        regular_work_type = self.env['hr.work.entry.type'].search([('code', '=', 'WORK100')], limit=1)
        late_type = self.env['hr.work.entry.type'].search([('code', '=', 'LATE')], limit=1)
        leave_types = self.env['hr.work.entry.type'].search([('is_leave', '=', True)])
        off_days_type= self.env['hr.work.entry.type'].search([('code', '=', 'UNJUSTIFIED')], limit=1)
        # Estructuras a excluir (ej. US)
        us_structures = self.env['hr.payroll.structure'].search([('code', '=', 'USMONTHLY')])

        # Separar los registros que manejamos aquí
        special_worked_days = self.env['hr.payslip.worked_days']
        other_worked_days = self.env['hr.payslip.worked_days']
        regular_worked_days = self.env['hr.payslip.worked_days']
        late_worked_days = self.env['hr.payslip.worked_days']
        leave_worked_days = self.env['hr.payslip.worked_days']
        off_days = self.env['hr.payslip.worked_days']
        for wd in self:
            _logger.info(wd.work_entry_type_id)
            if wd.payslip_id.struct_id in us_structures:
                other_worked_days |= wd
                continue

            # Validar que haya contrato
            if not wd.contract_id:
                wd.amount = 0.0
                continue

            # Verificar si es uno de nuestros tipos especiales
            if wd.work_entry_type_id in (overtime_day_type, overtime_night_type, guard_day_type, guard_night_type,recargo_nocturno_type):
                special_worked_days |= wd
            elif wd.work_entry_type_id == late_type:
                late_worked_days |= wd
            elif wd.work_entry_type_id == regular_work_type:
                regular_worked_days |= wd
            elif wd.work_entry_type_id in leave_types:  # <-- NUEVO
                leave_worked_days |= wd
            elif wd.work_entry_type_id in off_days_type:
                off_days |= wd
            else:
                other_worked_days |= wd
            _logger.info(off_days)

        # === Calcular montos para casos especiales (horas extras y guardias) ===
        for wd in leave_worked_days:
            contract = wd.payslip_id.contract_id
            days = wd.number_of_days  # ¡Importante! Usar días, no horas
            
            if not contract or wd.payslip_id.wage_type != 'monthly':
                wd.amount = 0.0
                continue
        
            # Calcular tasa diaria: salario mensual / 30
            daily_rate = contract.wage / 30.0
            wd.amount = daily_rate * days
        
            _logger.info(f"Ausencia {wd.work_entry_type_id.name}: {days} días → Monto = {wd.amount:.2f} (tasa diaria: {daily_rate:.2f})")
        for wd in special_worked_days:
            contract = wd.payslip_id.contract_id
            hours = wd.number_of_hours
            is_paid = wd.is_paid

            if not is_paid:
                wd.amount = 0.0
                continue

            # Calcular la tasa por hora según el tipo de contrato
            hourly_rate = 0.0
            if wd.payslip_id.wage_type == 'hourly':
                hourly_rate = contract.hourly_wage
            elif wd.payslip_id.wage_type == 'monthly':
                # Suponemos 30 días y 8 horas diarias
                daily_hours = 8  # Ajusta si tu regla es distinta
                monthly_days = 30
                total_monthly_hours = monthly_days * daily_hours
                hourly_rate = contract.wage / total_monthly_hours
            else:
                # Otros casos, usar el cálculo por defecto
                other_worked_days |= wd
                continue

            amount = 0.0
            if wd.work_entry_type_id == overtime_day_type:
                # HED = SH * 1.5
                rate = hourly_rate * 1.5
                amount = rate * hours

            elif wd.work_entry_type_id == overtime_night_type:
                # HEN = (SH + 30% recargo) * 2 → SH * 1.3 * 2 = SH * 2.6
                rate = hourly_rate * 2.0
                amount = rate * hours

            elif wd.work_entry_type_id == guard_day_type:
                # GD = 8 horas a tasa de HED (1.5)
                rate = hourly_rate * 1.5
                amount = rate * 8.0  # Guardia diurna: pago fijo por 8 horas

            elif wd.work_entry_type_id == guard_night_type:
                # GN = 8 horas a tasa de HEN (2.6)
                rate = hourly_rate * 2.6
                amount = rate * 8.0  # Guardia nocturna: pago fijo por 8 horas
            elif wd.work_entry_type_id == recargo_nocturno_type:  # ← NUEVO
                # RECARGO NOCTURNO = SH * 0.30
                amount = hourly_rate * 0.30 * hours
            elif wd.work_entry_type_id == off_days_type:
                contract = wd.payslip_id.contract_id
                days = wd.number_of_days  # ¡Importante! Usar días, no horas
                
                if not contract or wd.payslip_id.wage_type != 'monthly':
                    wd.amount = 0.0
                    continue
            
                # Calcular tasa diaria: salario mensual / 30
                daily_rate = contract.wage / 30.0
                wd.amount = daily_rate * days
            
                _logger.info(f"Ausencia {wd.work_entry_type_id.name}: {days} días → Monto = {wd.amount:.2f} (tasa diaria: {daily_rate:.2f})")
            wd.amount = amount
            _logger.info(wd.amount)
        # === Calcular deducción por retrasos confirmados ===
        for wd in late_worked_days:
            contract = wd.payslip_id.contract_id
            hours = wd.number_of_hours
            if not contract:
                wd.amount = 0.0
                continue

            # Calcular tarifa por hora (igual que en otros casos)
            hourly_rate = 0.0
            if wd.payslip_id.wage_type == 'hourly':
                hourly_rate = contract.hourly_wage
            elif wd.payslip_id.wage_type == 'monthly':
                daily_hours = 8
                monthly_days = 30
                hourly_rate = contract.wage / (monthly_days * daily_hours)
            else:
                wd.amount = 0.0
                continue

            # Aplicar deducción: horas de retraso * tarifa por hora (negativo)
            wd.amount = - (hourly_rate * hours)

            _logger.info(f"Deducción por retraso confirmado: {wd.amount} ({hours} horas a {hourly_rate}/h)")
        for wd in regular_worked_days:
            contract = wd.payslip_id.contract_id
            hours = wd.number_of_hours
            days = wd.number_of_days
            if wd.payslip_id.wage_type == 'hourly':
                wd.amount = contract.hourly_wage * hours
            elif wd.payslip_id.wage_type == 'monthly':
                hourly_rate = contract.wage / (30 * 8)
                daily_rate = contract.wage /30
                wd.amount = daily_rate * days
            else:
                wd.amount = 0.0
            _logger.info(f"Horas regulares: {wd.amount}")
        for wd in off_days:
            contract = wd.payslip_id.contract_id
            days = wd.number_of_days  # ¡Importante! Usar días, no horas
            
            if not contract or wd.payslip_id.wage_type != 'monthly':
                wd.amount = 0.0
                continue
            # Calcular tasa diaria: salario mensual / 30
            daily_rate = contract.wage / 30.0
            wd.amount = -(daily_rate * days)
        
            _logger.info(f"Ausencia {wd.work_entry_type_id.name}: {days} días → Monto = {wd.amount:.2f} (tasa diaria: {daily_rate:.2f})")
        # === Dejar el resto al cálculo original (incluye horas regulares, licencias, etc.) ===
        super(HrPayslipWorkedDays, other_worked_days)._compute_amount()