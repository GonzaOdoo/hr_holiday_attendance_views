from odoo import models, fields, api, Command
from datetime import date,datetime,timedelta
from dateutil.relativedelta import relativedelta

class PayrollStructure(models.Model):
    _inherit = 'hr.payroll.structure'

    is_final_liquidation = fields.Boolean('Es liquidación final')


class Payslip(models.Model):
    _inherit = 'hr.payslip'

    is_final_liquidation = fields.Boolean('Es liquidacion final',related='struct_id.is_final_liquidation')
    preaviso = fields.Date('Fecha de preaviso')
    last_day = fields.Date('Fecha de baja')
    final_type = fields.Selection([
        ('renuncia', 'Renuncia'),
        ('despido_justificado', 'Despido Justificado'),
        ('despido_injustificado', 'Despido Injustificado'),
        ('despido_injustificado_preaviso', 'Despido Injustificado (le notifico al colaborador Preaviso)'),
        ('periodo_prueba', 'Período de Prueba'),
        ('abandono_trabajo', 'Abandono de Trabajo'),
        ('jubilacion', 'Jubilación'),
        ('sustitucion_empleador', 'Sustitución del Empleador'),
        ('mutuo_acuerdo', 'Mutuo Acuerdo (colaboradores estables)'),
    ], string='Tipo de Baja')