# -*- coding: utf-8 -*-
from odoo import http,fields,models
from odoo.http import request
from odoo.exceptions import UserError, ValidationError
import urllib.parse
import logging
import base64
import logging
import json

_logger = logging.getLogger(__name__)
class LeavePortal(http.Controller):

    @http.route('/permisos', auth='user', website=True)
    def leave_form(self, **kw):
        user = request.env.user
        # Obtener empleado
        employee = user.employee_id
        if not employee and user.partner_id.employee_ids:
            employee = request.env['hr.employee'].sudo().search([
                ('id', 'in', user.partner_id.employee_ids.ids),
                ('active', '=', True)
            ], limit=1)
    
        if not employee:
            raise UserError("No tiene un empleado asociado.")
    
        # Obtener tipos de ausencia válidos
        leave_types = request.env['hr.leave.type'].sudo().search([
            '|',
            ('has_valid_allocation', '=', True),
            ('company_id', 'in', [employee.company_id.id, False])
        ])
    
        # >>> PRECALCULAR TODOS LOS SALDOS <<<
        balances = {}
        for lt in leave_types:
            if lt.requires_allocation == 'no':
                balances[lt.id] = {
                    'max_leaves': 'N/A',
                    'leaves_taken': 'N/A',
                    'virtual_remaining_leaves': 'No posee'
                }
            else:
                data = lt.sudo().get_allocation_data(employee, fields.Date.today())
                if employee in data and data[employee]:
                    vals = data[employee][0][1]
                    balances[lt.id] = {
                        'max_leaves': vals['max_leaves'],
                        'leaves_taken': vals['leaves_taken'],
                        'virtual_remaining_leaves': vals['virtual_remaining_leaves'],
                    }
                else:
                    balances[lt.id] = {
                        'max_leaves': 0,
                        'leaves_taken': 0,
                        'virtual_remaining_leaves': 0,
                    }
    
        # Valores del formulario (con fallback)
        selected_type_id = kw.get('holiday_status_id')
        if not selected_type_id and leave_types:
            selected_type_id = leave_types[0].id  # seleccionar el primero por defecto
        
        leave_vals = {
            'employee_id': employee.id,
            'request_date_from': kw.get('request_date_from') or fields.Date.today(),
            'request_date_to': kw.get('request_date_to') or fields.Date.today(),
            'holiday_status_id': int(selected_type_id) if selected_type_id else False,
            'replacement': int(kw.get('replacement') or 0) or False,
            'name': kw.get('name', ''),
            'tipo_enfermedad': kw.get('tipo_enfermedad', ''),
            'request_unit_hours': kw.get('request_unit_hours') == 'Yes',
            'request_hour_from': float(kw.get('request_hour_from') or 0.0),
            'request_hour_to': float(kw.get('request_hour_to') or 0.0),
        }
        leave = request.env['hr.leave'].sudo().new(leave_vals)
        return request.render('hr_holiday_attendance_views.leave_form_custom', {
            'leave': leave,
            'employees': request.env['hr.employee'].sudo().search([('active', '=', True), ('company_id', 'in', [employee.company_id.id, False])]),
            'leave_types': leave_types,
            'balances': balances,  # <-- PASAMOS TODOS LOS SALDOS
            'balances_json': json.dumps(balances),
            'error': kw.get('error'),
        })

    @http.route('/permisos/submit', auth='user', website=True, methods=['POST'])
    def leave_submit(self, **post):
        """Procesa el envío del formulario."""
        user = request.env.user
        # Obtener empleado
        employee = user.employee_id
        if not employee and user.partner_id.employee_ids:
            employee = request.env['hr.employee'].sudo().search([
                ('id', 'in', user.partner_id.employee_ids.ids),
                ('active', '=', True)
            ], limit=1)
    
        if not employee:
            raise UserError("No tiene un empleado asociado.")
        try:
            # Validar y convertir valores
            with request.env.cr.savepoint():
                vals = {
                    'employee_id': employee.id,  # ¡El empleado siempre es el del usuario!
                    'replacement': int(post.get('replacement', 0)) if post.get('replacement', 0) else False,
                    'holiday_status_id': int(post['holiday_status_id']),
                    'request_date_from': post['request_date_from'],
                    'request_date_to': post['request_date_to'],
                    'name': post.get('name', ''),
                    'reason_text': post.get('name', ''),
                    'tipo_enfermedad': post.get('tipo_enfermedad', ''),
                }
                # Crear la solicitud
                leave = request.env['hr.leave'].sudo().create(vals)
    
                # Manejar archivo adjunto si existe
                if 'attachment_ids' in request.httprequest.files:
                    file = request.httprequest.files['attachment_ids']
                    if file.filename:
                        request.env['ir.attachment'].sudo().create({
                            'name': file.filename,
                            'datas': base64.b64encode(file.read()),
                            'res_model': 'hr.leave',
                            'res_id': leave.id,
                        })
    
                return request.redirect('/permisos/success')

        except (ValidationError, UserError) as e:
            # Extraer solo el mensaje del error y codificarlo correctamente
            error_message = str(e)
            # Limpiar el mensaje de error: quitar saltos de línea y espacios extras
            clean_error = " ".join(error_message.split())
            # Codificar para URL
            encoded_error = urllib.parse.quote_plus(clean_error)
            
            # Reconstruir los parámetros del formulario
            params = []
            for k, v in post.items():
                if k != 'error':  # Excluir el parámetro error si existe
                    params.append(f'{k}={urllib.parse.quote_plus(str(v))}')
            
            # Construir la URL de redirección
            redirect_url = '/permisos'
            if params:
                redirect_url += '?' + '&'.join(params)
            redirect_url += f'&error={encoded_error}'
            
            return request.redirect(redirect_url)
            
        except Exception as e:
            # Manejar otros errores inesperados
            _logger.exception("Error inesperado al crear solicitud de ausencia")
            _logger.info(e)
            clean_error = "Ocurrió un error en el formulario, por favor verifique los datos. Si el problema persiste comuniquese con el administrador"
            encoded_error = urllib.parse.quote_plus(clean_error)
            
            # Reconstruir los parámetros del formulario
            params = []
            for k, v in post.items():
                if k != 'error':
                    params.append(f'{k}={urllib.parse.quote_plus(str(v))}')
            
            redirect_url = '/permisos'
            if params:
                redirect_url += '?' + '&'.join(params)
            redirect_url += f'&error={encoded_error}'
            
            return request.redirect(redirect_url)


    @http.route('/mis-permisos', auth='user', website=True)
    def my_leaves(self, **kw):
        user = request.env.user
        employee = user.employee_id

        if not employee and user.partner_id.employee_ids:
            employee = request.env['hr.employee'].sudo().search([
                ('id', 'in', user.partner_id.employee_ids.ids),
                ('active', '=', True)
            ], limit=1)
    
        if not employee:
            raise UserError("No tiene un empleado asociado.")
    
        # Obtener parámetros de búsqueda
        search = kw.get('search', '')
        state = kw.get('state', '')  # 'confirm', 'validate', 'refuse', etc.
        date_from = kw.get('date_from', '')
        date_to = kw.get('date_to', '')
    
        # Dominio base
        domain = [('employee_id', '=', employee.id)]
    
        # Filtros
        if search:
            domain += ['|', ('name', 'ilike', search), ('holiday_status_id.name', 'ilike', search)]
        if state:
            domain += [('state', '=', state)]
        if date_from:
            domain += [('request_date_from', '>=', date_from)]
        if date_to:
            domain += [('request_date_to', '<=', date_to)]
    
        leaves = request.env['hr.leave'].sudo().search(domain, order='create_date desc')
    
        # Opciones para los filtros
        state_options = {
            '': 'Todos',
            'confirm': 'Pendiente',
            'validate': 'Aprobado',
            'refuse': 'Rechazado',
            'cancel': 'Cancelado',
        }
    
        return request.render('hr_holiday_attendance_views.my_leaves_list', {
            'leaves': leaves,
            'search': search,
            'current_state': state,
            'state_options': state_options,
            'date_from': date_from,
            'date_to': date_to,
        })


    @http.route('/permisos/success', auth='user', website=True)
    def leave_success(self, **kw):
        return request.render('hr_holiday_attendance_views.leave_success_page')