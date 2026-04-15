# -*- coding: utf-8 -*-
from odoo import http,fields,models
from odoo.http import request
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta, time
import pytz
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
        leave_types_shift = {lt.id: lt.shift_change for lt in leave_types}
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
            #'shift_start': float(kw.get('shift_start') or 0.0),
            #'shift_end': float(kw.get('shift_end') or 0.0),
        }
        leave = request.env['hr.leave'].sudo().new(leave_vals)
        return request.render('hr_holiday_attendance_views.leave_form_custom', {
            'leave': leave,
            'employees': request.env['hr.employee'].sudo().search([('active', '=', True), ('company_id', 'in', [employee.company_id.id, False])]),
            'leave_types': leave_types,
            'balances': balances,  # <-- PASAMOS TODOS LOS SALDOS
            'balances_json': json.dumps(balances),
            'leave_types_shift': leave_types_shift,
            'leave_types_shift_json': json.dumps(leave_types_shift),
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
                shift_start = self._time_to_float(post.get('shift_start'))
                shift_end = self._time_to_float(post.get('shift_end'))
                request_unit_hours = bool(shift_start and shift_end)
                request_hour_from = shift_start or 0.0
                request_hour_to = shift_end or 0.0
                date_from = fields.Date.from_string(post['request_date_from'])
                date_to = fields.Date.from_string(post['request_date_to'])
                if shift_end <= shift_start and date_from == date_to:
                    # Cruce de medianoche: el final es al día siguiente
                    date_to = date_to + timedelta(days=1)
                date = fields.Date.from_string(post['request_date_from'])
                start = self._time_to_float(post.get('shift_start'))
                end = self._time_to_float(post.get('shift_end'))
                
                date_from = datetime.combine(date, time()) + timedelta(hours=start)
                
                if end <= start:
                    date_to = datetime.combine(date + timedelta(days=1), time()) + timedelta(hours=end)
                else:
                    date_to = datetime.combine(date, time()) + timedelta(hours=end)

                
                vals = {
                    'employee_id': employee.id,  # ¡El empleado siempre es el del usuario!
                    'replacement': int(post.get('replacement', 0)) if post.get('replacement', 0) else False,
                    'holiday_status_id': int(post['holiday_status_id']),
                    'request_date_from':date_from,
                    'request_date_to': date_to,
                    'name': post.get('name', ''),
                    'reason_text': post.get('name', ''),
                    'tipo_enfermedad': post.get('tipo_enfermedad', ''),
                    'shift_start': shift_start,
                    'shift_end': shift_end,
                    
                }
                _logger.info(vals)
                if shift_start and shift_end:
                    suggested_calendar = self._find_best_matching_calendar(
                        employee,
                        date_from,
                        date_to,
                        shift_start,
                        shift_end
                    )
                    if suggested_calendar:
                        vals['calendar_days'] = suggested_calendar.id
                # Crear la solicitud
                _logger.info(f"Creando leave con vals: {vals}")
                _logger.info(f"date_from calculado: {vals.get('date_from')}")
                _logger.info(f"date_to calculado: {vals.get('date_to')}")
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


    def _find_best_matching_calendar(self, employee, date_from, date_to, shift_start, shift_end):
        """
        Encuentra el resource.calendar cuyas líneas de attendance 
        más se aproximan al rango de horas solicitado para las fechas dadas.
        
        :return: record de resource.calendar o False
        """
        if not shift_start or not shift_end or not date_from or not date_to:
            return False
        
        from datetime import timedelta
        from odoo import fields
        
        # Calendarios relevantes para la compañía del empleado
        calendars = request.env['resource.calendar'].sudo().search([
            '|',
            ('company_id', '=', employee.company_id.id),
            ('company_id', '=', False),
            ('active', '=', True)
        ])
        
        if not calendars:
            return False
        
        # Conversión segura de fechas
        try:
            d_from = fields.Date.from_string(date_from) if isinstance(date_from, str) else date_from
            d_to = fields.Date.from_string(date_to) if isinstance(date_to, str) else date_to
        except:
            return False
        
        # Normalizar horas (manejar casos donde shift_end < shift_start = turno nocturno)
        req_start = float(shift_start) % 24
        req_end = float(shift_end) % 24
        is_overnight = req_end < req_start
        
        best_calendar = False
        best_avg_diff = float('inf')
        
        for calendar in calendars:
            if not calendar.attendance_ids:
                continue
            
            total_diff = 0
            days_evaluated = 0
            
            current_date = d_from
            while current_date <= d_to:
                dayofweek = str(current_date.weekday())  # '0'=Lunes, '6'=Domingo
                
                # Filtrar líneas de asistencia para este día de semana
                attendances = calendar.attendance_ids.filtered(
                    lambda a: a.dayofweek == dayofweek 
                    and (not a.date_from or a.date_from <= current_date)
                    and (not a.date_to or a.date_to >= current_date)
                )
                
                if attendances:
                    att = attendances[0]  # Tomamos la primera coincidencia
                    att_start = att.hour_from
                    att_end = att.hour_to
                    
                    # Calcular diferencia (manejando turnos nocturnos)
                    if is_overnight or (att_end < att_start):
                        # Para turnos nocturnos: comparar con lógica circular
                        diff_start = min(abs(req_start - att_start), abs(req_start - (att_start + 24)))
                        diff_end = min(abs(req_end - att_end), abs(req_end - (att_end + 24)))
                    else:
                        diff_start = abs(req_start - att_start)
                        diff_end = abs(req_end - att_end)
                    
                    # Penalizar si la duración es muy diferente
                    req_duration = (req_end + 24 if is_overnight else req_end) - req_start
                    att_duration = (att_end + 24 if att_end < att_start else att_end) - att_start
                    duration_penalty = abs(req_duration - att_duration) * 0.5
                    
                    total_diff += diff_start + diff_end + duration_penalty
                    days_evaluated += 1
                
                current_date += timedelta(days=1)
            
            if days_evaluated > 0:
                avg_diff = total_diff / days_evaluated
                # Actualizar si es mejor y está dentro de un umbral razonable (ej: 3 horas de diferencia promedio)
                if avg_diff < best_avg_diff and avg_diff <= 3.0:
                    best_avg_diff = avg_diff
                    best_calendar = calendar
        
        return best_calendar


    def _time_to_float(self, time_str):
        if not time_str:
            return 0.0
        h, m = map(int, time_str.split(':'))
        return h + (m / 60.0)