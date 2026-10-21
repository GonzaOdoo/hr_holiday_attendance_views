from odoo import api, fields, models, _
from odoo.exceptions import UserError,ValidationError


class DepartureReason(models.Model):
    _inherit = "hr.departure.reason"

    relation_code = fields.Char('Codigo')

class HrEmployee(models.Model):
    _inherit = 'hr.employee'
    preaviso_date =  fields.Date(string="Fecha de preaviso", required=False)
    final_payslip_id = fields.Many2one(
        "hr.payslip",
        string="Liquidación final",
        compute="_compute_final_payslip_id",
    )
    final_liquidation_status = fields.Selection(
        [
            ("active", "Activo"),
            ("in_process", "En proceso de liquidación final"),
            ("inactive", "Inactivo"),
        ],
        string="Estado laboral",
        default="active",
        required=True,
        copy=False,
        tracking=True,
    )
    
    def _compute_final_payslip_id(self):
        Payslip = self.env["hr.payslip"]

        for employee in self:
            employee.final_payslip_id = Payslip.search([
                ("employee_id", "=", employee.id),
                ("struct_id.is_final_liquidation", "=", True),
            ], order="id desc", limit=1)


    def action_archive(self):
        if not self.env.context.get("allow_final_liquidation_archive"):
            Payslip = self.env["hr.payslip"]
    
            for employee in self:
                confirmed_payslip = Payslip.search([
                    ("employee_id", "=", employee.id),
                    ("struct_id.is_final_liquidation", "=", True),
                    ("state", "in", ["done", "paid"]),
                ], limit=1)
    
                if not confirmed_payslip:
                    raise UserError(_(
                        "No se puede archivar al empleado %s porque no "
                        "tiene una liquidación final confirmada."
                    ) % employee.name)
    
        res = super().action_archive()
    
        self.write({
            "final_liquidation_status": "inactive",
        })
    
        return res
class HrDepartureWizard(models.TransientModel):
    _inherit = 'hr.departure.wizard'

    preaviso_date =  fields.Date(string="Fecha de preaviso", required=False)
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


    def action_register_departure(self):
        res = super().action_register_departure()

        for wizard in self:
            
            wizard.employee_id.preaviso_date = wizard.preaviso_date

        return res

    
    def action_create_final_liquidation(self):
        self.ensure_one()
    
        employee = self.employee_id
        departure_reason = self.departure_reason_id
    
        if employee.final_payslip_id:
            raise UserError(_(
                "El empleado ya tiene una liquidación final. "
                "No se puede generar otra."
            ))
    
        # Buscar el contrato vigente del empleado
        contract = employee.contract_id
    
        if not contract:
            raise UserError(_(
                "El empleado no tiene un contrato asociado."
            ))
    
        # Buscar la estructura de liquidación final correspondiente
        final_structure = self.env['hr.payroll.structure'].search([
            ('is_final_liquidation', '=', True),
            ('type_id', '=', contract.structure_type_id.id),
        ], limit=1)
    
        if not final_structure:
            raise UserError(_(
                "No se encontró una estructura de liquidación final "
                "para el tipo de estructura salarial del contrato."
            ))

    
        if employee.final_payslip_id:
            raise UserError(_(
                "El empleado ya tiene una liquidación final. "
                "No se puede generar otra."
            ))
    
   
        if not departure_reason.relation_code:
            raise UserError(_(
                "El motivo de baja '%s' no tiene configurado el código "
                "de tipo de baja."
            ) % departure_reason.display_name)
    

    
        employee.write({
            "departure_reason_id": departure_reason.id,
            "departure_description": self.departure_description,
            "departure_date": self.departure_date,
            "preaviso_date": self.preaviso_date,
            "final_liquidation_status": "in_process",
        })
    
        payslip = self.env["hr.payslip"].create({
            "name": _("Liquidación final - %s") % employee.name,
            "employee_id": employee.id,
            "contract_id": contract.id,
            "struct_id": final_structure.id,
            "preaviso": self.preaviso_date,
            "last_day": self.departure_date,
            "final_type": departure_reason.relation_code,
        })
    
        return {
            "type": "ir.actions.act_window",
            "name": _("Liquidación final"),
            "res_model": "hr.payslip",
            "view_mode": "form",
            "res_id": payslip.id,
            "target": "current",
        }