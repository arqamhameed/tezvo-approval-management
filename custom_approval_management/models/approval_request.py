# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError, UserError
from datetime import datetime
import logging

_logger = logging.getLogger(__name__)


class ApprovalRequest(models.Model):
    _name = 'approval.request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Approval Request'
    _order = 'submitted_date DESC, name DESC'

    _INTERNAL_CREATE_CONTEXT_KEY = 'approval_request_internal_create'

    name = fields.Char(
        required=True,
        copy=False,
        readonly=True,
        default='New',
        string='Request Number'
    )
    document_type_id = fields.Many2one(
        'approval.document.type',
        required=True,
        string='Document Type'
    )
    res_model = fields.Char(required=True, string='Document Model')
    res_id = fields.Integer(required=True, string='Document ID')
    document_ref = fields.Char(string='Document Reference')
    partner_id = fields.Many2one('res.partner', string='Partner')
    amount_total = fields.Float(string='Amount')
    submitted_by = fields.Many2one(
        'res.users',
        default=lambda self: self.env.user,
        readonly=True,
        string='Submitted By'
    )
    submitted_date = fields.Datetime(
        default=lambda self: fields.Datetime.now(),
        readonly=True,
        string='Submitted Date'
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        string='Company'
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('waiting', 'Waiting Approval'),
            ('partial', 'Partially Approved'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        string='Status'
    )
    current_level_id = fields.Many2one(
        'approval.level',
        string='Current Approval Level'
    )
    cycle_no = fields.Integer(
        default=1,
        readonly=True,
        string='Approval Cycle',
        help='Cycle number for approval requests on same document (1st approval, 2nd resubmission, etc.)'
    )
    request_line_ids = fields.One2many(
        'approval.request.line',
        'request_id',
        string='Approval Lines'
    )
    history_ids = fields.One2many(
        'approval.history',
        'request_id',
        string='Approval History'
    )
    can_current_user_approve = fields.Boolean(
        compute='_compute_can_current_user_approve',
        search='_search_can_current_user_approve',
        compute_sudo=False,
        string='Can Current User Approve'
    )
    can_current_user_reject = fields.Boolean(
        compute='_compute_can_current_user_reject',
        compute_sudo=False,
        string='Can Current User Reject'
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not (
            self.env.context.get(self._INTERNAL_CREATE_CONTEXT_KEY)
            or self.env.context.get('install_mode')
        ):
            raise UserError('Approval requests are created automatically from business documents.')
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('custom.approval.request')
        records = super().create(vals_list)
        for record in records:
            if record._is_ready_for_submit_notification():
                record._on_submitted_for_approval()
        return records

    def copy(self, default=None):
        raise UserError('Approval requests cannot be duplicated.')

    def unlink(self):
        if self.env.context.get('module_uninstall'):
            return super().unlink()
        raise UserError('Approval requests cannot be deleted. Please cancel the request instead.')

    def write(self, vals):
        result = super().write(vals)
        for record in self:
            if record._is_ready_for_submit_notification():
                record._on_submitted_for_approval()
        return result

    def _is_ready_for_submit_notification(self):
        """Return True only when request is fully ready for first submit email."""
        self.ensure_one()
        if self.state not in ('waiting', 'partial'):
            return False
        if not self.current_level_id:
            return False
        if not self.request_line_ids:
            return False
        if self.history_ids.filtered(lambda h: h.action == 'submitted'):
            return False
        return True

    # ------------------------------------------------------------------
    # URL helpers for emails
    # ------------------------------------------------------------------

    def _get_approval_request_url(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return f'{base_url}/web#id={self.id}&model=approval.request&view_type=form'

    def _get_related_document_url(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return ''
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return f'{base_url}/web#id={self.res_id}&model={self.res_model}&view_type=form'

    # ------------------------------------------------------------------
    # Generic chatter logging
    # ------------------------------------------------------------------

    def _post_approval_log(self, message, subtype_xmlid='mail.mt_note'):
        self.ensure_one()
        self.message_post(body=message, subtype_xmlid=subtype_xmlid)

    def _post_related_document_log(self, message, subtype_xmlid='mail.mt_note'):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return
        try:
            document = self.env[self.res_model].browse(self.res_id).exists()
            if not document or not hasattr(document, 'message_post'):
                return
            document.check_access_rights('read')
            document.check_access_rule('read')
            document.message_post(body=message, subtype_xmlid=subtype_xmlid)
        except Exception:
            # Logging on related document is best effort and must never block
            # approval actions.
            return

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    def _get_eligible_rights_for_level(self, level):
        self.ensure_one()
        if not level:
            return self.env['approval.user.right']

        rights = self.env['approval.user.right'].sudo().search([
            ('document_type_id', '=', self.document_type_id.id),
            ('level_id', '=', level.id),
            ('active', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])
        today = fields.Date.today()
        eligible = self.env['approval.user.right']
        for right in rights:
            if not right.user_id.active:
                continue
            if right.expiry_date and right.expiry_date < today:
                continue
            if not self._right_allows_amount(right):
                continue
            eligible |= right
        return eligible

    def _get_all_rights_for_level(self, level):
        """Get all rights for a level without amount filtering (for delegation lookup).
        
        Used for delegation logic to ensure delegated approvers are found
        even when document amount is outside normal approval limits.
        """
        self.ensure_one()
        if not level:
            return self.env['approval.user.right']

        rights = self.env['approval.user.right'].sudo().search([
            ('document_type_id', '=', self.document_type_id.id),
            ('level_id', '=', level.id),
            ('active', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])
        today = fields.Date.today()
        eligible = self.env['approval.user.right']
        for right in rights:
            if not right.user_id.active:
                continue
            if right.expiry_date and right.expiry_date < today:
                continue
            # NOTE: NOT checking amount here - delegation applies to all amounts
            eligible |= right
        return eligible

    def _get_current_level_eligible_rights(self):
        self.ensure_one()
        return self._get_eligible_rights_for_level(self.current_level_id)

    def _get_current_level_approver_users(self):
        self.ensure_one()
        current_line = self._get_current_line()
        if current_line and current_line.required_user_ids:
            return current_line.required_user_ids.filtered(lambda u: u.active)
        return self._get_current_level_eligible_rights().mapped('user_id')

    def _get_line_direct_users(self, line):
        self.ensure_one()
        if not line:
            return self.env['res.users']
        if line.required_user_ids:
            return line.required_user_ids.filtered(lambda u: u.active)
        return self._get_all_rights_for_level(line.level_id).mapped('user_id')

    def _get_active_delegations_for_level(self, level, original_users, delegated_user=None):
        self.ensure_one()
        if not level or not original_users:
            return self.env['approval.delegation']

        delegation_model = self.env['approval.delegation']
        delegations = delegation_model.browse()
        delegated_user_id = delegated_user.id if hasattr(delegated_user, 'id') else delegated_user

        for original_user in original_users:
            user_delegations = delegation_model._get_active_delegations_for_user(
                original_user,
                document_type=self.document_type_id,
                approval_level=level,
                company=self.company_id,
            )
            if delegated_user_id:
                user_delegations = user_delegations.filtered(
                    lambda delegation: delegation.delegated_user_id.id == delegated_user_id
                )
            delegations |= user_delegations

        return delegations

    def _get_delegation_context_for_user(self, user, current_line=None):
        self.ensure_one()
        line = current_line or self._get_current_line()
        if not line or line.state != 'pending':
            return False, False
        if not self.current_level_id or line.level_id != self.current_level_id:
            return False, False

        # Use _get_all_rights_for_level to get approvers without amount filtering
        # This ensures delegation works for all documents at a level
        direct_users = self._get_line_direct_users(line)
        if not direct_users:
            _logger.debug(
                f'[Approval] No direct users found for level {line.level_id.name}, '
                f'request {self.name}'
            )
            return False, False

        delegations = self._get_active_delegations_for_level(line.level_id, direct_users, delegated_user=user)
        if not delegations:
            _logger.debug(
                f'[Approval] No delegation found for user {user.name} from {[u.name for u in direct_users]}, '
                f'request {self.name}, level {line.level_id.name}'
            )
            return False, False

        delegation = delegations.sorted('id')[:1]
        _logger.debug(
            f'[Approval] Delegation found: {user.name} delegated from {delegation.original_user_id.name}, '
            f'request {self.name}, level {line.level_id.name}'
        )
        return delegation, delegation.original_user_id

    def _get_delegated_user_data_for_level(self, level):
        self.ensure_one()
        current_line = self._get_current_line()
        direct_users = self._get_line_direct_users(current_line) if current_line and current_line.level_id == level else self._get_all_rights_for_level(level).mapped('user_id')
        delegations = self._get_active_delegations_for_level(level, direct_users)
        result = {}
        for delegation in delegations:
            delegated = delegation.delegated_user_id
            if not delegated:
                continue
            entry = result.setdefault(delegated.id, {
                'user': delegated,
                'original_users': self.env['res.users'],
                'delegations': self.env['approval.delegation'],
            })
            entry['original_users'] |= delegation.original_user_id
            entry['delegations'] |= delegation
        return result

    def _get_current_approver_emails(self):
        self.ensure_one()
        current_line = self._get_current_line()
        users = self._get_line_direct_users(current_line).filtered(lambda u: u.email) if current_line else self._get_current_level_approver_users().filtered(lambda u: u.email)
        return ','.join(sorted(set(users.mapped('email'))))

    def _send_delegated_approver_notifications(self, event, line, reason=''):
        self.ensure_one()
        if not line:
            return
        if not self._notification_enabled(event):
            return
        if not self._level_allows_notification(line, event):
            return

        direct_emails = set(self._get_line_direct_users(line).filtered(lambda u: u.email).mapped('email'))
        delegated_data = self._get_delegated_user_data_for_level(line.level_id)

        for item in delegated_data.values():
            delegated_user = item['user']
            delegated_email = delegated_user.email
            if not delegated_email:
                self._post_approval_log(
                    f'Delegation notification skipped: delegated approver {delegated_user.name} has no email address.'
                )
                continue
            if delegated_email in direct_emails:
                continue

            original_names = ', '.join(item['original_users'].mapped('name'))
            delegated_note = (
                'You are receiving this approval request as a delegated approver '
                f'on behalf of {original_names}.'
            )
            sent = self._send_python_notification(
                event,
                delegated_email,
                reason=reason,
                delegated_note=delegated_note,
            )
            if sent:
                self._post_approval_log(
                    f'Delegation email sent to {delegated_user.name} on behalf of {original_names}.'
                )

    def _get_current_approver_names(self):
        self.ensure_one()
        return ', '.join(self._get_current_level_approver_users().mapped('name'))

    def _log_missing_email_for_users(self, users, audience_label):
        self.ensure_one()
        missing_users = users.filtered(lambda u: not u.email)
        for missing_user in missing_users:
            self._post_approval_log(
                f'Email notification was not sent to {missing_user.name} ({audience_label}) because no email address is configured.'
            )

    def _get_related_document_owner_user(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return self.env['res.users']
        document = self.env[self.res_model].sudo().browse(self.res_id).exists()
        if not document:
            return self.env['res.users']

        for field_name in ('user_id', 'invoice_user_id'):
            if field_name in document._fields:
                owner_user = document[field_name]
                if owner_user and owner_user._name == 'res.users':
                    return owner_user

        if 'create_uid' in document._fields and document.create_uid:
            return document.create_uid

        return self.env['res.users']

    def _get_requester_email(self):
        self.ensure_one()
        return self.submitted_by.email or ''

    def _level_allows_notification(self, rule_line, event_code):
        self.ensure_one()
        if not rule_line:
            return False
        if not event_code:
            return False
        return event_code in set(rule_line.notification_event_ids.mapped('code'))

    def _get_level_approver_emails_from_line(self, line):
        self.ensure_one()
        if not line:
            return ''
        users = self._get_line_direct_users(line).filtered(lambda u: u.email)
        return ','.join(sorted(set(users.mapped('email'))))

    def _notification_enabled(self, event):
        self.ensure_one()
        doc_type = self.document_type_id
        if not doc_type:
            return False
        mapping = {
            'submit': doc_type.notify_on_submit,
            'next_level': doc_type.notify_on_next_level,
            'final_approval': doc_type.notify_on_final_approval,
            'rejection': doc_type.notify_on_rejection,
            'cancellation': doc_type.notify_on_cancellation,
        }
        return bool(mapping.get(event))

    def _send_template_notification(self, template_xmlid, event, reason='', recipients=None, line=None):
        self.ensure_one()
        if not self._notification_enabled(event):
            return False

        target_line = line
        if event in ('submit', 'next_level') and not target_line:
            target_line = self._get_current_line()
        if target_line and not self._level_allows_notification(target_line, event):
            return False

        if recipients is None:
            if event in ('submit', 'next_level'):
                recipients = self._get_current_approver_emails()
            else:
                recipients = self._get_requester_email()
        if not recipients:
            self._post_approval_log(
                'Email notification could not be sent because no valid recipient email was found.',
            )
            return False

        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if template and not (line and recipients is not None):
            # XML template exists, use it
            try:
                template.send_mail(self.id, force_send=True, raise_exception=True)
                return True
            except Exception:
                self._post_approval_log(
                    'Email notification could not be sent. '
                    'Please check recipient email address or outgoing mail configuration.',
                )
                return False
        else:
            # XML template not found, fall back to Python-generated email
            return self._send_python_notification(event, recipients, reason=reason)

    def _send_python_notification(self, event, recipients, reason='', delegated_note=''):
        """Send email using Python-generated HTML as fallback when XML template is missing."""
        self.ensure_one()
        if not recipients:
            return False

        try:
            subject = self._get_notification_subject(event)
            body_html = self._get_notification_body_html(event, reason=reason, delegated_note=delegated_note)
            email_from = self.env.user.email_formatted or self.env.company.email or ''

            if not email_from:
                self._post_approval_log(
                    'Email notification could not be sent. Sender email address not configured.',
                )
                return False

            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': recipients,
                'email_from': email_from,
                'auto_delete': False,
            }
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.sudo().send(raise_exception=True)
            return True
        except Exception:
            self._post_approval_log(
                'Email notification could not be sent. '
                'Please check recipient email address or outgoing mail configuration.',
            )
            return False

    def _get_notification_subject(self, event):
        """Generate email subject based on event type."""
        self.ensure_one()
        doc_ref = self.document_ref or self.name
        level_name = self.current_level_id.name or ''

        if event == 'submit':
            return f'[Approval Required] {doc_ref} - {level_name}'
        elif event == 'next_level':
            return f'[Next Level Approval] {doc_ref} - {level_name}'
        elif event == 'final_approval':
            return f'[Approved] {doc_ref}'
        elif event == 'rejection':
            return f'[Rejected] {doc_ref}'
        elif event == 'cancellation':
            return f'[Cancelled] {doc_ref}'
        return f'[Approval Notification] {doc_ref}'

    def _get_notification_body_html(self, event, reason='', delegated_note=''):
        """Generate Outlook-safe table-based HTML email body based on event type."""
        self.ensure_one()

        color_map = {
            'submit':         {'header': '#1f6feb', 'badge': 'WAITING APPROVAL'},
            'next_level':     {'header': '#7b3fe4', 'badge': 'NEXT LEVEL'},
            'final_approval': {'header': '#1f9d55', 'badge': 'APPROVED'},
            'rejection':      {'header': '#dc3545', 'badge': 'REJECTED'},
            'cancellation':   {'header': '#f08c00', 'badge': 'CANCELLED'},
        }
        c = color_map.get(event, color_map['submit'])
        hdr = c['header']
        badge = c['badge']

        msg_map = {
            'submit':         'A document is waiting for your approval at the current level.',
            'next_level':     'The request has moved to your approval level.',
            'final_approval': 'Your document has been fully approved.',
            'rejection':      'Your approval request was rejected.',
            'cancellation':   'The approval request was cancelled.',
        }
        status_msg = msg_map.get(event, '')

        def esc(val):
            return str(val).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

        detail_rows = [
            ('Approval Request',   self.name or '-'),
            ('Document Type',      self.document_type_id.name or '-'),
            ('Document Reference', self.document_ref or '-'),
            ('Partner',            self.partner_id.name or '-'),
            ('Amount',             '%.2f' % (self.amount_total or 0.0)),
            ('Company',            self.company_id.name or '-'),
        ]
        if event in ('submit', 'next_level'):
            detail_rows.append(('Current Approval Level', self.current_level_id.name or '-'))
        detail_rows += [
            ('Submitted By',   self.submitted_by.name or '-'),
            ('Submitted Date', str(self.submitted_date or '-')),
        ]
        if event == 'rejection' and reason:
            detail_rows.append(('Rejection Reason', reason))
        if delegated_note:
            detail_rows.append(('Delegation Note', delegated_note))

        rows_html = ''
        for lbl, val in detail_rows:
            rows_html += (
                '<tr>'
                f'<td width="220" style="padding:8px 10px;border:1px solid #e5e9f2;'
                f'font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#243447;'
                f'vertical-align:top;mso-line-height-rule:exactly;line-height:20px;">'
                f'<strong>{esc(lbl)}</strong></td>'
                f'<td style="padding:8px 10px;border:1px solid #e5e9f2;'
                f'font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#243447;'
                f'vertical-align:top;mso-line-height-rule:exactly;line-height:20px;">'
                f'{esc(val)}</td>'
                '</tr>'
            )

            approval_request_url = esc(self._get_approval_request_url() or '#')
            related_document_url = esc(self._get_related_document_url() or '#')

        html = (
            '<!DOCTYPE html>'
            '<html><head>'
            '<meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '</head>'
            '<body style="margin:0;padding:0;background-color:#f4f6f8;">'
            # Outer background table (100% width)
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background-color:#f4f6f8;">'
            '<tr><td align="center" style="padding:24px 0;">'
            # MSO conditional for fixed-width centering
            '<!--[if mso]><table width="680" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->'
            # Card table – width:100%/max-width:680px for mobile; MSO ghost table fixes Outlook centering
            '<table cellpadding="0" cellspacing="0" border="0" align="center" '
            'style="width:100%;max-width:680px;background-color:#ffffff;border:1px solid #e5e9f2;">'
            # --- HEADER ROW ---
            '<tr>'
            f'<td bgcolor="{hdr}" style="background-color:{hdr};padding:16px 20px;">'
            '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            '<tr>'
            f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:20px;'
            f'font-weight:bold;color:#ffffff;mso-line-height-rule:exactly;line-height:28px;">'
            'Approval Notification'
            '</td>'
            '</tr>'
            '<tr>'
            '<td style="padding-top:8px;">'
            '<table cellpadding="0" cellspacing="0" border="0">'
            '<tr>'
            f'<td bgcolor="#ffffff" style="background-color:#ffffff;padding:4px 10px;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:12px;'
            f'font-weight:bold;color:{hdr};'
            f'mso-line-height-rule:exactly;line-height:18px;">'
            f'{badge}'
            '</td>'
            '</tr>'
            '</table>'
            '</td>'
            '</tr>'
            '</table>'
            '</td>'
            '</tr>'
            # --- STATUS MESSAGE ROW ---
            '<tr>'
            f'<td style="padding:16px 20px 8px 20px;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#243447;'
            f'mso-line-height-rule:exactly;line-height:22px;">'
            f'{esc(status_msg)}'
            '</td>'
            '</tr>'
            # --- DETAILS TABLE ROW ---
            '<tr>'
            '<td style="padding:0 20px 16px 20px;">'
            '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            + rows_html +
            '</table>'
            '</td>'
            '</tr>'
            # --- BUTTONS ROW ---
            '<tr>'
            '<td style="padding:8px 20px 20px 20px;">'
            '<table cellpadding="0" cellspacing="0" border="0">'
            '<tr>'
            f'<td bgcolor="{hdr}" style="background-color:{hdr};padding:10px 16px;">'
            f'<a href="{approval_request_url}" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'color:#ffffff;text-decoration:none;display:block;'
            f'mso-line-height-rule:exactly;line-height:20px;">Open Approval Request</a>'
            '</td>'
            '<td width="12" style="font-size:0;line-height:0;">&nbsp;</td>'
            '<td bgcolor="#5b6b7f" style="background-color:#5b6b7f;padding:10px 16px;">'
            f'<a href="{related_document_url}" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            'color:#ffffff;text-decoration:none;display:block;'
            'mso-line-height-rule:exactly;line-height:20px;">Open Document</a>'
            '</td>'
            '</tr>'
            '</table>'
            '</td>'
            '</tr>'
            # --- FOOTER ROW ---
            '<tr>'
            '<td style="padding:0 20px 20px 20px;'
            'font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#6b7785;'
            'mso-line-height-rule:exactly;line-height:18px;">'
            'This is an automated approval notification from Odoo.'
            '</td>'
            '</tr>'
            '</table>'
            '<!--[if mso]></td></tr></table><![endif]-->'
            '</td></tr>'
            '</table>'
            '</body></html>'
        )
        return html

    def _on_submitted_for_approval(self):
        self.ensure_one()
        if not self._is_ready_for_submit_notification():
            return
        approver_names = self._get_current_approver_names() or 'No eligible approver'
        current_line = self._get_current_line()
        if current_line:
            self._log_missing_email_for_users(self._get_line_direct_users(current_line), 'current level approver')
        email_enabled = self._level_allows_notification(current_line, 'submit')
        self._create_history('submitted', 'Approval request submitted.')
        if email_enabled:
            self._post_approval_log(
                f'Approval request submitted. Current approval level: {self.current_level_id.name}. '
                f'Notification sent to: {approver_names}.'
            )
        else:
            self._post_approval_log(
                f'Approval request submitted. Current approval level: {self.current_level_id.name}. '
                'Approver email notifications are disabled for this level.'
            )
        self._post_related_document_log(
            f'Approval request submitted for this document. Current approval level: {self.current_level_id.name}.'
        )
        if email_enabled and self._send_template_notification(
            'custom_approval_management.mail_template_approval_required_current',
            'submit',
            line=current_line,
        ):
            self._post_approval_log(
                f'Approval notification email sent to current approver(s): {approver_names}.'
            )
        self._send_delegated_approver_notifications('submit', current_line)

    def _on_moved_to_next_level(self):
        self.ensure_one()
        approver_names = self._get_current_approver_names() or 'No eligible approver'
        current_line = self._get_current_line()
        if current_line:
            self._log_missing_email_for_users(self._get_line_direct_users(current_line), 'next level approver')
        email_enabled = self._level_allows_notification(current_line, 'next_level')
        if email_enabled:
            self._post_approval_log(
                f'Approval moved to next level: {self.current_level_id.name}. '
                f'Notification sent to: {approver_names}.'
            )
        else:
            self._post_approval_log(
                f'Approval moved to next level: {self.current_level_id.name}. '
                'Approver email notifications are disabled for this level.'
            )
        self._post_related_document_log(
            f'Approval moved to next level: {self.current_level_id.name}.'
        )
        if email_enabled and self._send_template_notification(
            'custom_approval_management.mail_template_approval_required_next_level',
            'next_level',
            line=current_line,
        ):
            self._post_approval_log(
                f'Approval notification email sent to next level approver(s): {approver_names}.'
            )
        self._send_delegated_approver_notifications('next_level', current_line)

    def _on_final_approved_notification(self, line=None):
        self.ensure_one()
        self._post_approval_log(
            f'Approval completed. Final approval completed by {self.env.user.name}.'
        )
        self._post_related_document_log(
            'Approval completed. This document has been fully approved.'
        )
        requester = self.submitted_by
        if requester and not requester.email:
            self._post_approval_log(
                f'Email notification was not sent to {requester.name} (request submitter) because no email address is configured.'
            )
        self._send_template_notification(
            'custom_approval_management.mail_template_approval_final_completed',
            'final_approval',
        )
        owner_user = self._get_related_document_owner_user()
        if owner_user and owner_user.id != self.submitted_by.id:
            if owner_user.email:
                sent = self._send_python_notification('final_approval', owner_user.email)
                if sent:
                    self._post_approval_log(
                        f'Final approval notification email sent to document owner: {owner_user.name}.'
                    )
            else:
                self._post_approval_log(
                    f'Email notification was not sent to {owner_user.name} (document owner) because no email address is configured.'
                )
        if line and self._level_allows_notification(line, 'final_approval'):
            self._send_template_notification(
                'custom_approval_management.mail_template_approval_final_completed',
                'final_approval',
                recipients=self._get_level_approver_emails_from_line(line),
                line=line,
            )
            self._send_delegated_approver_notifications('final_approval', line)

    def _on_rejected_notification(self, reason='', line=None):
        self.ensure_one()
        reason_text = reason or '-'
        requester = self.submitted_by
        if requester and not requester.email:
            self._post_approval_log(
                f'Email notification was not sent to {requester.name} (request submitter) because no email address is configured.'
            )
        self._post_approval_log(
            f'Approval rejected by {self.env.user.name}. Reason: {reason_text}'
        )
        self._post_related_document_log(
            f'Approval rejected by {self.env.user.name}. Reason: {reason_text}'
        )
        self._send_template_notification(
            'custom_approval_management.mail_template_approval_rejected_requester',
            'rejection',
            reason=reason,
        )
        if line and self._level_allows_notification(line, 'rejection'):
            self._send_template_notification(
                'custom_approval_management.mail_template_approval_rejected_requester',
                'rejection',
                reason=reason,
                recipients=self._get_level_approver_emails_from_line(line),
                line=line,
            )
            self._send_delegated_approver_notifications('rejection', line, reason=reason)

    def _on_cancelled_notification(self, line=None):
        self.ensure_one()
        self._post_approval_log(
            f'Approval request cancelled by {self.env.user.name}.'
        )
        self._post_related_document_log('Approval request cancelled.')
        self._send_template_notification(
            'custom_approval_management.mail_template_approval_cancelled_requester',
            'cancellation',
        )
        if line and self._level_allows_notification(line, 'cancellation'):
            self._send_template_notification(
                'custom_approval_management.mail_template_approval_cancelled_requester',
                'cancellation',
                recipients=self._get_level_approver_emails_from_line(line),
                line=line,
            )
            self._send_delegated_approver_notifications('cancellation', line)

    @api.depends_context('uid')
    def _compute_can_current_user_approve(self):
        for record in self:
            record.can_current_user_approve = record._can_user_act_on_current_level(self.env.user)

    def _search_can_current_user_approve(self, operator, value):
        """Search support for dynamic user-specific pending eligibility."""
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return [('id', '=', 0)]

        my_ids = self._get_my_pending_request_ids()
        positive = [('id', 'in', my_ids)]
        negative = [('id', 'not in', my_ids)]

        if operator == '=':
            return positive if value else negative
        return negative if value else positive

    @api.depends_context('uid')
    def _compute_can_current_user_reject(self):
        for record in self:
            if record.state not in ('waiting', 'partial'):
                record.can_current_user_reject = False
                continue
            current_line = record._get_current_line()
            can_reject = False
            if current_line and current_line.state == 'pending':
                can_reject = record.can_current_user_approve
            record.can_current_user_reject = can_reject

    def _get_current_line(self):
        """Get the pending request line for current_level_id.

        Falls back to the first pending line by sequence if current_level_id is
        not set for backward compatibility with legacy records.
        """
        self.ensure_one()
        if self.current_level_id:
            current_line = self.request_line_ids.filtered(
                lambda x: x.state == 'pending' and x.level_id == self.current_level_id
            )[:1]
            if current_line:
                return current_line
        return self.request_line_ids.filtered(
            lambda x: x.state == 'pending'
        ).sorted('sequence', reverse=False)[:1]

    def _right_allows_amount(self, right):
        """Return True if request amount is inside the user's right limits."""
        self.ensure_one()
        if self.amount_total < right.value_lower_limit:
            return False
        if right.value_upper_limit > 0 and self.amount_total > right.value_upper_limit:
            return False
        return True

    def _can_user_act_on_current_level(self, user):
        """Check if a user can approve/reject this request at current active level."""
        self.ensure_one()
        if self.state not in ('waiting', 'partial'):
            return False

        current_line = self._get_current_line()
        if not current_line or current_line.state != 'pending':
            return False
        if not self.current_level_id or current_line.level_id != self.current_level_id:
            return False

        user_id = user.id
        submitted_by_id = self.submitted_by.id

        if user_id == submitted_by_id and not current_line.allow_same_user:
            return False

        direct_user_ids = set(self._get_current_level_approver_users().ids)
        if user_id in direct_user_ids:
            if user_id in current_line.approved_user_ids.ids:
                return False
            return True

        delegation, original_user = self._get_delegation_context_for_user(user, current_line=current_line)
        if delegation and original_user:
            if original_user.id in current_line.approved_user_ids.ids:
                return False
            if original_user.id == self.submitted_by.id and not current_line.allow_same_user:
                return False
            return True

        return False

    @api.model
    def _get_my_pending_request_ids(self):
        """Return requests actionable by current user on current active level only."""
        # sudo() ensures all candidate requests are visible regardless of the
        # calling user's record-level access on approval.request.
        requests = self.sudo().search([('state', 'in', ['waiting', 'partial'])])
        return requests.filtered(
            lambda r: r._can_user_act_on_current_level(self.env.user)
        ).ids

    @api.model
    def _get_my_pending_approvals_action(self, target='current'):
        eligible_ids = self._get_my_pending_request_ids()
        action_context = dict(self.env.context)
        # Avoid carrying web client routing params back into the next action,
        # which can grow URL/state segments on repeated object-button refreshes.
        for key in ('params', 'active_id', 'active_ids', 'active_model'):
            action_context.pop(key, None)
        search_view = self.env.ref(
            'custom_approval_management.view_approval_request_search',
            raise_if_not_found=False,
        )
        list_view = self.env.ref(
            'custom_approval_management.view_approval_request_my_pending_tree',
            raise_if_not_found=False,
        )
        form_view = self.env.ref(
            'custom_approval_management.view_approval_request_form',
            raise_if_not_found=False,
        )
        views = []
        if list_view:
            views.append((list_view.id, 'list'))
        if form_view:
            views.append((form_view.id, 'form'))
        return {
            'type': 'ir.actions.act_window',
            'name': 'My Pending Approvals',
            'res_model': 'approval.request',
            'view_mode': 'list,form',
            'views': views or False,
            'domain': [('id', 'in', eligible_ids)],
            'context': action_context,
            'target': target,
            'search_view_id': [search_view.id, 'search'] if search_view else False,
        }

    @api.model
    def action_my_pending_approvals(self):
        """Return a window action filtered to requests the current user can approve.

        Called from the My Pending Approvals menu via an ir.actions.server so
        that eligible IDs are calculated in Python (safe, no non-stored field
        domain issues) rather than relying on a computed-field domain in XML.
        """
        return self._get_my_pending_approvals_action(target='current')

    def action_refresh_my_pending_approvals(self):
        """Re-run dynamic My Pending eligibility and reload the current action."""
        return self.env['approval.request'].with_context(self.env.context)._get_my_pending_approvals_action(target='main')

    def action_open_document(self):
        """Open the referenced document."""
        self.ensure_one()
        if not self.res_model or not self.res_id:
            raise UserError('Document model or ID not found.')

        model_name = self.res_model
        res_id = self.res_id

        # Read technical model metadata with sudo so business users do not need
        # ir.model access to open their related approval document.
        technical_model = self.env['ir.model'].sudo().search([
            ('model', '=', model_name),
            ('transient', '=', False),
        ], limit=1)
        if not technical_model:
            raise UserError('The linked document model is invalid or no longer available.')

        document = self.env[model_name].browse(res_id)

        # Do not sudo business records: enforce normal user ACL + record rules.
        has_read_right = document.check_access_rights('read', raise_exception=False)
        if not has_read_right:
            raise UserError('You do not have access to open this document.')

        try:
            document.check_access_rule('read')
        except AccessError:
            raise UserError('You do not have access to open this document.')

        if not document.exists():
            raise UserError('The linked document was not found or is no longer accessible.')

        return {
            'type': 'ir.actions.act_window',
            'name': self.name or 'Document',
            'res_model': model_name,
            'res_id': res_id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_approve(self):
        """Approve the current request line."""
        self.ensure_one()
        if self.state not in ['waiting', 'partial']:
            raise UserError('Only waiting or partially approved requests can be approved.')
        if not self._can_user_act_on_current_level(self.env.user):
            raise UserError('You do not have permission to approve this request.')

        current_line = self._get_current_line()
        if not current_line:
            raise UserError('No pending approval line found.')

        delegation, original_user = self._get_delegation_context_for_user(self.env.user, current_line=current_line)
        approval_identity = original_user if delegation else self.env.user

        if approval_identity.id in current_line.approved_user_ids.ids:
            raise UserError('This approval has already been completed for the current approver.')

        current_line.approved_user_ids = [(4, approval_identity.id)]
        acted_level_id = current_line.level_id.id
        acted_user_id = self.env.user.id

        if len(current_line.approved_user_ids) >= current_line.minimum_user_count:
            current_line.state = 'approved'
            current_line.approved_date = fields.Datetime.now()
            if delegation:
                current_line.is_delegated_action = True
                current_line.delegated_original_user_id = original_user.id
                current_line.delegated_user_id = self.env.user.id
                current_line.delegation_id = delegation.id
                delegation_note = (
                    f'{self.env.user.name} approved this request on behalf of {original_user.name}.'
                )
                self._post_approval_log(delegation_note)
                self._post_related_document_log(delegation_note)
            else:
                self._post_approval_log(f'{self.env.user.name} approved this request.')
                self._post_related_document_log(
                    f'{self.env.user.name} approved this request.'
                )
            moved_to_next = self._move_to_next_level()
            if moved_to_next:
                self._on_moved_to_next_level()
            else:
                self._execute_related_document_callback(
                    '_approval_on_final_approved',
                )
                self._on_final_approved_notification(line=current_line)
        else:
            self.state = 'partial'

        self._sync_related_document_state()
        self._execute_related_document_callback(
            '_approval_on_request_line_approved',
            current_line,
            approval_identity,
        )
        delegated_remarks = False
        if delegation:
            delegated_remarks = f'Approved by {self.env.user.name} on behalf of {original_user.name}.'
        self._create_history(
            'approved',
            remarks=delegated_remarks,
            level_id=acted_level_id,
            user_id=acted_user_id,
            is_delegated_action=bool(delegation),
            delegated_original_user_id=original_user.id if original_user else False,
            delegated_user_id=self.env.user.id if delegation else False,
            delegation_id=delegation.id if delegation else False,
        )

    def action_open_reject_wizard(self):
        """Open reject wizard."""
        self.ensure_one()
        if self.state not in ['waiting', 'partial']:
            raise UserError('Only waiting or partially approved requests can be rejected.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reject Approval Request',
            'res_model': 'approval.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_approval_request_id': self.id},
        }

    def action_reject(self, reason=''):
        """Reject the current request line."""
        self.ensure_one()
        if self.state not in ['waiting', 'partial']:
            raise UserError('Only waiting or partially approved requests can be rejected.')
        if not self._can_user_act_on_current_level(self.env.user):
            raise UserError('You do not have permission to reject this request.')

        current_line = self._get_current_line()
        if not current_line:
            raise UserError('No pending approval line found.')

        delegation, original_user = self._get_delegation_context_for_user(self.env.user, current_line=current_line)

        current_line.state = 'rejected'
        current_line.rejected_user_id = self.env.user.id
        current_line.rejected_date = fields.Datetime.now()
        current_line.remarks = reason
        if delegation:
            current_line.is_delegated_action = True
            current_line.delegated_original_user_id = original_user.id
            current_line.delegated_user_id = self.env.user.id
            current_line.delegation_id = delegation.id
        acted_level_id = current_line.level_id.id
        acted_user_id = self.env.user.id

        self.state = 'rejected'
        self._sync_related_document_state()
        self._execute_related_document_callback(
            '_approval_on_request_line_rejected',
            current_line,
            self.env.user,
            reason,
        )

        history_remarks = reason or ''
        if delegation:
            delegation_note = (
                f'{self.env.user.name} rejected this request on behalf of {original_user.name} '
                f'under delegation {delegation.name}. Reason: {reason or "-"}'
            )
            self._post_approval_log(delegation_note)
            self._post_related_document_log(delegation_note)
            history_remarks = f'Rejected by {self.env.user.name} on behalf of {original_user.name}. Reason: {reason or "-"}'

        self._create_history(
            'rejected',
            remarks=history_remarks,
            level_id=acted_level_id,
            user_id=acted_user_id,
            is_delegated_action=bool(delegation),
            delegated_original_user_id=original_user.id if original_user else False,
            delegated_user_id=self.env.user.id if delegation else False,
            delegation_id=delegation.id if delegation else False,
        )
        self._on_rejected_notification(reason=reason, line=current_line)

    def action_cancel(self):
        """Cancel the approval request."""
        self.ensure_one()
        if self.state not in ['draft', 'waiting', 'partial']:
            raise UserError('Only draft or pending approval requests can be cancelled.')

        current_line = self._get_current_line()
        pending_lines = self.request_line_ids.filtered(lambda l: l.state == 'pending')
        if pending_lines:
            pending_lines.write({
                'state': 'cancelled',
                'remarks': 'Approval request was cancelled.',
            })

        self.state = 'cancelled'
        self.current_level_id = False
        self._sync_related_document_state()
        self._execute_related_document_callback(
            '_approval_on_request_cancelled',
            current_line,
            self.env.user,
        )
        self._create_history(
            'cancelled',
            'Approval request was cancelled.',
            level_id=current_line.level_id.id if current_line else False,
            notification_line=current_line,
        )

    def _move_to_next_level(self):
        """Move current level to the next pending level by sequence.

        Sets state to:
          - partial: if more levels remain pending
          - approved: if no pending level remains
        """
        self.ensure_one()

        current_line = self.request_line_ids.filtered(
            lambda x: x.state == 'approved'
        ).sorted('sequence', reverse=False)[-1:]

        next_line = self.request_line_ids.filtered(
            lambda x: x.state == 'pending' and x.sequence > current_line.sequence and x.mandatory
        ).sorted('sequence')[:1]

        if not next_line:
            next_line = self.request_line_ids.filtered(
                lambda x: x.state == 'pending' and x.sequence > current_line.sequence
            ).sorted('sequence')[:1]

        if next_line:
            self.current_level_id = next_line.level_id.id
            self.state = 'partial'
        else:
            self.state = 'approved'
            self.current_level_id = False

        self._sync_related_document_state()
        return bool(next_line)

    def _sync_related_document_state(self):
        """Propagate approval state to the referenced business document."""
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return

        document = self.env[self.res_model].browse(self.res_id).exists()
        if not document:
            return

        # Keep sync narrow: only approval-module tracking fields are updated on
        # the related document, using controlled sudo to avoid requiring
        # business-document write ACLs for approvers.
        write_vals = {}
        if 'approval_request_id' in document._fields:
            write_vals['approval_request_id'] = self.id
        if 'current_approval_level_id' in document._fields:
            write_vals['current_approval_level_id'] = self.current_level_id.id or False
        if 'approval_state' in document._fields:
            state_mapping = {
                'draft': 'draft',
                'waiting': 'waiting',
                'partial': 'partial',
                'approved': 'approved',
                'rejected': 'rejected',
                'cancelled': 'cancelled',
            }
            mapped_state = state_mapping.get(self.state)
            if mapped_state:
                write_vals['approval_state'] = mapped_state

        if write_vals:
            document.sudo().with_context(
                approval_internal_state_sync=True
            ).write(write_vals)

    def _execute_related_document_callback(self, method_name, *args):
        """Run non-critical document callback without blocking approval actions."""
        self.ensure_one()
        if not self.res_model or not self.res_id or not method_name:
            return

        document = self.env[self.res_model].sudo().browse(self.res_id).exists()
        if not document or not hasattr(document, method_name):
            return

        try:
            getattr(
                document.sudo().with_context(approval_internal_state_sync=True),
                method_name,
            )(*args)
        except Exception as exc:
            _logger.warning(
                'Related document callback %s failed for %s,%s: %s',
                method_name,
                self.res_model,
                self.res_id,
                exc,
            )

    def _create_history(
        self,
        action,
        remarks='',
        level_id=False,
        user_id=False,
        notification_line=None,
        is_delegated_action=False,
        delegated_original_user_id=False,
        delegated_user_id=False,
        delegation_id=False,
    ):
        """Create approval history record."""
        self.ensure_one()
        if not level_id:
            current_line = self._get_current_line()
            level_id = current_line.level_id.id if current_line else self.current_level_id.id
        if not user_id:
            user_id = self.env.user.id

        # Approvers may not have create ACL on approval.history; use sudo for audit trail persistence.
        record = self.env['approval.history'].sudo().create({
            'request_id': self.id,
            'res_model': self.res_model,
            'res_id': self.res_id,
            'level_id': level_id,
            'user_id': user_id,
            'action': action,
            'remarks': remarks,
            'is_delegated_action': is_delegated_action,
            'delegated_original_user_id': delegated_original_user_id,
            'delegated_user_id': delegated_user_id,
            'delegation_id': delegation_id,
        })

        action_label = dict(self.env['approval.history']._fields['action'].selection).get(action, action)
        message = remarks or f'Approval history updated: {action_label}.'
        self._post_approval_log(message)
        self._post_related_document_log(message)

        if action == 'cancelled':
            self._send_template_notification(
                'custom_approval_management.mail_template_approval_cancelled_requester',
                'cancellation',
            )
            if notification_line and self._level_allows_notification(notification_line, 'cancellation'):
                self._send_template_notification(
                    'custom_approval_management.mail_template_approval_cancelled_requester',
                    'cancellation',
                    recipients=self._get_level_approver_emails_from_line(notification_line),
                    line=notification_line,
                )
                self._send_delegated_approver_notifications('cancellation', notification_line)
