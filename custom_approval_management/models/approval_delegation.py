# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class ApprovalDelegation(models.Model):
    _name = 'approval.delegation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Approval Delegation'
    _order = 'create_date desc, id desc'

    _EDITABLE_FIELDS = {
        'name', 'original_user_id', 'delegated_user_id', 'date_from', 'date_to',
        'document_type_id', 'approval_level_id', 'company_id', 'reason',
    }

    name = fields.Char(required=True, readonly=True, copy=False, default='/', tracking=True)
    display_name_text = fields.Char(compute='_compute_display_name_text', string='Display Name')
    original_user_id = fields.Many2one('res.users', required=True, string='Original Approver', tracking=True)
    delegated_user_id = fields.Many2one('res.users', required=True, string='Delegated Approver', tracking=True)
    date_from = fields.Date(required=True, string='From Date', tracking=True)
    date_to = fields.Date(required=True, string='To Date', tracking=True)
    document_type_id = fields.Many2one('approval.document.type', string='Document Type', tracking=True)
    approval_level_id = fields.Many2one('approval.level', string='Approval Level', tracking=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company, tracking=True)
    reason = fields.Text(tracking=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('waiting_original_approval', 'Waiting Original Approval'),
            ('active', 'Active'),
            ('approved_pending_start', 'Approved Pending Start'),
            ('expired', 'Expired'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        copy=False,
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)
    is_delegation_active = fields.Boolean(compute='_compute_is_delegation_active', string='Active')
    submitted_by_id = fields.Many2one('res.users', string='Submitted By', readonly=True, copy=False)
    submitted_on = fields.Datetime(string='Submitted On', readonly=True, copy=False)
    original_approved_by_id = fields.Many2one('res.users', string='Original Approved By', readonly=True, copy=False)
    original_approved_on = fields.Datetime(string='Original Approved On', readonly=True, copy=False)
    original_rejected_by_id = fields.Many2one('res.users', string='Original Rejected By', readonly=True, copy=False)
    original_rejected_on = fields.Datetime(string='Original Rejected On', readonly=True, copy=False)
    original_rejection_reason = fields.Text(string='Original Rejection Reason', readonly=True, copy=False)
    delegated_accepted_by_id = fields.Many2one('res.users', string='Delegated Accepted By', readonly=True, copy=False)
    delegated_accepted_on = fields.Datetime(string='Delegated Accepted On', readonly=True, copy=False)
    delegated_rejected_by_id = fields.Many2one('res.users', string='Delegated Rejected By', readonly=True, copy=False)
    delegated_rejected_on = fields.Datetime(string='Delegated Rejected On', readonly=True, copy=False)
    delegated_rejection_reason = fields.Text(string='Delegated Rejection Reason', readonly=True, copy=False)
    cancelled_by_id = fields.Many2one('res.users', string='Cancelled By', readonly=True, copy=False)
    cancelled_on = fields.Datetime(string='Cancelled On', readonly=True, copy=False)
    cancellation_reason = fields.Text(string='Cancellation Reason', readonly=True, copy=False)
    expired_on = fields.Datetime(string='Expired On', readonly=True, copy=False)
    approval_request_id = fields.Many2one('approval.request', string='Approval Request', readonly=True, copy=False)
    current_approval_level_id = fields.Many2one('approval.level', string='Current Approval Level', readonly=True, copy=False)

    can_amend_after_approval = fields.Boolean(compute='_compute_can_amend_after_approval')
    can_submit = fields.Boolean(compute='_compute_action_flags')
    can_view_request = fields.Boolean(compute='_compute_action_flags')
    can_set_draft = fields.Boolean(compute='_compute_action_flags')
    can_cancel = fields.Boolean(compute='_compute_action_flags')

    @api.depends('original_user_id', 'delegated_user_id', 'date_from', 'date_to')
    def _compute_display_name_text(self):
        for record in self:
            if record.name and record.name != '/':
                record.display_name_text = f'{record.name} - {record.original_user_id.name or ""} -> {record.delegated_user_id.name or ""}'.strip()
            else:
                record.display_name_text = 'Approval Delegation'

    def name_get(self):
        result = []
        for record in self:
            label = record.name if record.name and record.name != '/' else 'Approval Delegation'
            if record.original_user_id and record.delegated_user_id:
                label = f'{label} - {record.original_user_id.name} -> {record.delegated_user_id.name}'
            result.append((record.id, label))
        return result

    @api.depends('state', 'date_from', 'date_to', 'original_user_id', 'delegated_user_id')
    def _compute_is_delegation_active(self):
        today = fields.Date.today()
        for record in self:
            record.is_delegation_active = bool(
                record.state == 'active'
                and record.date_from
                and record.date_to
                and record.date_from <= today <= record.date_to
                and record.original_user_id.active
                and record.delegated_user_id.active
            )

    def _auto_init(self):
        result = super()._auto_init()
        try:
            self.env.cr.execute(
                "UPDATE approval_delegation SET active = %s WHERE active IS NOT TRUE",
                (True,),
            )
        except Exception:
            pass
        return result

    def _user_is_delegation_officer(self):
        return self.env.user.has_group('custom_approval_management.group_custom_approval_delegation_officer')

    def _user_is_admin_or_officer(self):
        return bool(
            self.env.user.has_group('custom_approval_management.group_custom_approval_admin')
            or self._user_is_delegation_officer()
        )

    @api.depends('state', 'original_user_id', 'delegated_user_id')
    def _compute_can_amend_after_approval(self):
        can_amend = self._user_is_delegation_officer()
        for record in self:
            record.can_amend_after_approval = can_amend

    @api.depends('state')
    def _compute_action_flags(self):
        for record in self:
            record.can_submit = record.state == 'draft'
            record.can_view_request = bool(record.approval_request_id)
            record.can_set_draft = record.state in ('rejected', 'cancelled') and self._user_is_admin_or_officer()
            record.can_cancel = record.state not in ('expired', 'cancelled') and self._user_is_admin_or_officer()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('approval.delegation') or '/'
            vals.setdefault('active', True)
            vals.setdefault('state', 'draft')
            vals.setdefault('submitted_by_id', False)
            vals.setdefault('submitted_on', False)
        records = super().create(vals_list)
        records._sync_runtime_state(post_message=False)
        return records

    def copy(self, default=None):
        raise UserError('Approval Delegation records cannot be duplicated.')

    def unlink(self):
        if self.env.context.get('module_uninstall'):
            return super().unlink()
        raise UserError('Approval Delegation records cannot be deleted. Please cancel the delegation instead.')

    def write(self, vals):
        if self.env.context.get('skip_delegation_write_guard'):
            return super().write(vals)

        allowed_context = (
            self.env.context.get('approval_delegation_workflow_action')
            or self.env.context.get('install_mode')
            or self.env.context.get('module_uninstall')
        )
        touching_manual_fields = bool(set(vals).intersection(self._EDITABLE_FIELDS))

        for record in self:
            if record.state != 'draft' and touching_manual_fields and not (allowed_context or record.can_amend_after_approval):
                raise UserError('Only draft delegations can be edited. Use Delegation Officer access for amendments.')

        result = super().write(vals)
        if touching_manual_fields or 'state' in vals or 'active' in vals:
            self._sync_runtime_state(post_message=True)
        return result

    @api.constrains('original_user_id', 'delegated_user_id')
    def _check_users(self):
        for record in self:
            if record.original_user_id == record.delegated_user_id:
                raise ValidationError('Original approver and delegated approver cannot be the same user.')
            if record.original_user_id and not record.original_user_id.active:
                raise ValidationError('Original approver must be an active user.')
            if record.delegated_user_id and not record.delegated_user_id.active:
                raise ValidationError('Delegated approver must be an active user.')
            if record.delegated_user_id and not record.delegated_user_id.has_group('custom_approval_management.group_custom_approval_user'):
                raise ValidationError('Delegated approver must have Approval User access.')

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for record in self:
            if record.date_from and record.date_to and record.date_from > record.date_to:
                raise ValidationError('From Date must be less than or equal to To Date.')

    @api.constrains('original_user_id', 'delegated_user_id', 'date_from', 'date_to', 'document_type_id', 'approval_level_id', 'company_id', 'state')
    def _check_overlap_and_circular(self):
        for record in self:
            if not record.original_user_id or not record.delegated_user_id or not record.date_from or not record.date_to:
                continue
            if record.state in ('rejected', 'cancelled', 'expired'):
                continue

            if record.state != 'draft' and record._check_date_conflict():
                raise ValidationError('An overlapping delegation already exists for this original approver and scope.')

            circular_domain = [
                ('id', '!=', record.id),
                ('original_user_id', '=', record.delegated_user_id.id),
                ('delegated_user_id', '=', record.original_user_id.id),
                ('state', 'not in', ('rejected', 'cancelled', 'expired')),
                ('date_from', '<=', record.date_to),
                ('date_to', '>=', record.date_from),
            ]
            if self.search_count(circular_domain):
                raise ValidationError('Circular delegation is not allowed for overlapping date ranges.')

    def _check_date_conflict(self):
        self.ensure_one()
        if not self.original_user_id or not self.date_from or not self.date_to:
            return self.browse()

        current_id = self.id if isinstance(self.id, int) else 0
        domain = [
            ('id', '!=', current_id),
            ('original_user_id', '=', self.original_user_id.id),
            ('state', 'not in', ('rejected', 'cancelled', 'expired')),
            ('date_from', '<=', self.date_to),
            ('date_to', '>=', self.date_from),
        ]
        if self.company_id:
            domain += ['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id)]
        if self.document_type_id:
            domain += ['|', ('document_type_id', '=', False), ('document_type_id', '=', self.document_type_id.id)]
        if self.approval_level_id:
            domain += ['|', ('approval_level_id', '=', False), ('approval_level_id', '=', self.approval_level_id.id)]

        candidates = self.sudo().search(domain)
        conflict_states = {
            'waiting_approval',
            'waiting_original_approval',
            'waiting_delegated_acceptance',
            'approved_pending_start',
            'active',
        }

        def _is_conflict(existing):
            request_state = existing.approval_request_id.state if existing.approval_request_id else False
            if existing.state in conflict_states:
                return True
            if request_state in ('waiting', 'partial'):
                return True
            if request_state == 'approved' and existing.state in ('active', 'approved_pending_start'):
                return True
            return False

        return candidates.filtered(_is_conflict)

    def _format_conflict_message(self, conflict):
        self.ensure_one()
        state_label = dict(self._fields['state'].selection).get(conflict.state, conflict.state)
        if conflict.state in ('waiting_approval', 'waiting_original_approval', 'waiting_delegated_acceptance'):
            state_label = 'Waiting Approval'

        before_conflict_end = conflict.date_from - timedelta(days=1)
        after_conflict_start = conflict.date_to + timedelta(days=1)
        return (
            'Delegation date range conflict found.\n\n'
            f'Original Approver: {self.original_user_id.name}\n'
            f'Requested Range: {self.date_from} to {self.date_to}\n\n'
            'Existing Delegation:\n'
            f"{conflict.display_name_text or conflict.name}\n"
            f'Range: {conflict.date_from} to {conflict.date_to}\n'
            f'State: {state_label}\n\n'
            'Suggested options:\n'
            f'- Use a date range ending before {conflict.date_from}\n'
            f'- Or start from {after_conflict_start}\n'
            f'- Example previous end date: {before_conflict_end}\n\n'
            'Please select a different date range or cancel/reject the existing delegation first.'
        )

    @api.onchange('original_user_id', 'date_from', 'date_to', 'company_id', 'document_type_id', 'approval_level_id')
    def _onchange_date_conflict_warning(self):
        for record in self:
            if not record.original_user_id or not record.date_from or not record.date_to:
                continue
            conflicts = record._check_date_conflict().sorted(lambda r: (r.date_from or fields.Date.today(), r.id))
            if not conflicts:
                continue
            conflict = conflicts[:1]
            return {
                'warning': {
                    'title': 'Delegation Date Conflict',
                    'message': record._format_conflict_message(conflict),
                }
            }
        return {}

    def _delegation_is_currently_valid(self, today=None):
        self.ensure_one()
        today = today or fields.Date.today()
        return bool(
            self.state == 'active'
            and self.active
            and self.date_from
            and self.date_to
            and self.date_from <= today <= self.date_to
            and self.original_user_id.active
            and self.delegated_user_id.active
        )

    def _effective_state_from_dates(self, today=None):
        self.ensure_one()
        today = today or fields.Date.today()
        if today > self.date_to:
            return 'expired'
        if today < self.date_from:
            return 'approved_pending_start'
        return 'active'

    def _sync_runtime_state(self, post_message=True):
        today = fields.Date.today()
        for record in self.sudo():
            if record.state in ('draft', 'rejected', 'cancelled'):
                continue
            if (
                record.state == 'waiting_original_approval'
                and record.approval_request_id
                and record.approval_request_id.state == 'approved'
            ):
                effective_state = record._effective_state_from_dates(today)
                vals = {'state': effective_state}
                if effective_state == 'expired':
                    vals['expired_on'] = record.expired_on or fields.Datetime.now()
                record.with_context(
                    skip_delegation_write_guard=True,
                    approval_delegation_workflow_action=True,
                ).write(vals)
                if post_message:
                    record.message_post(
                        body='Delegation state synchronized from approved approval request.',
                        subtype_xmlid='mail.mt_note',
                    )
                continue
            if record.date_to and today > record.date_to and record.state != 'expired':
                record.with_context(skip_delegation_write_guard=True, approval_delegation_workflow_action=True).write({
                    'state': 'expired',
                    'expired_on': record.expired_on or fields.Datetime.now(),
                })
                if post_message:
                    record.message_post(body='Delegation expired automatically because the end date has passed.', subtype_xmlid='mail.mt_note')
                continue
            if record.state == 'approved_pending_start' and record.date_from <= today <= record.date_to:
                record.with_context(skip_delegation_write_guard=True, approval_delegation_workflow_action=True).write({
                    'state': 'active',
                })
                if post_message:
                    record.message_post(body='Delegation activated automatically because the start date has arrived.', subtype_xmlid='mail.mt_note')

    def _get_delegation_request_levels(self):
        self.ensure_one()
        original_level = self.env['approval.level'].search([
            ('code', '=', 'approval_delegation_original'),
            ('company_id', 'in', [False, self.company_id.id]),
            ('active', '=', True),
        ], order='company_id desc, id desc', limit=1)
        delegated_level = self.env['approval.level'].search([
            ('code', '=', 'approval_delegation_delegated'),
            ('company_id', 'in', [False, self.company_id.id]),
            ('active', '=', True),
        ], order='company_id desc, id desc', limit=1)
        if not original_level or not delegated_level:
            raise UserError('Delegation approval levels are not configured. Please install the module data again.')
        return original_level, delegated_level

    def _get_delegation_document_type(self):
        self.ensure_one()
        doc_type = self.env['approval.document.type'].search([
            ('document_nature', '=', 'approval_delegation'),
            ('model_name', '=', 'approval.delegation'),
            ('active', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ], order='company_id desc, id desc', limit=1)
        if not doc_type:
            raise UserError('Approval Delegation document type is not configured.')
        return doc_type

    def _approval_create_request(self):
        self.ensure_one()
        if not self.id:
            raise UserError('Please save the delegation before submitting for approval.')
        if self.approval_request_id and self.approval_request_id.state not in ('cancelled', 'rejected', 'approved'):
            raise UserError('An approval request already exists for this delegation.')

        original_level, delegated_level = self._get_delegation_request_levels()
        doc_type = self._get_delegation_document_type()
        request_vals = {
            'name': '/',
            'document_type_id': doc_type.id,
            'res_model': 'approval.delegation',
            'res_id': self.id,
            'document_ref': self.display_name_text or self.name,
            'submitted_by': self.env.user.id,
            'submitted_date': fields.Datetime.now(),
            'company_id': self.company_id.id,
            'state': 'waiting',
            'current_level_id': original_level.id,
            'request_line_ids': [
                (0, 0, {
                    'sequence': 10,
                    'level_id': original_level.id,
                    'minimum_user_count': 1,
                    'allow_same_user': True,
                    'mandatory': True,
                    'required_user_ids': [(6, 0, [self.original_user_id.id])],
                }),
                (0, 0, {
                    'sequence': 20,
                    'level_id': delegated_level.id,
                    'minimum_user_count': 1,
                    'allow_same_user': True,
                    'mandatory': True,
                    'required_user_ids': [(6, 0, [self.delegated_user_id.id])],
                }),
            ],
        }
        request = self.env['approval.request'].with_context(approval_request_internal_create=True).create(request_vals)
        first_line = request.request_line_ids.sorted('sequence')[:1]
        self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write({
            'approval_request_id': request.id,
            'current_approval_level_id': first_line.level_id.id if first_line else False,
            'state': 'waiting_original_approval',
        })
        self.message_post(body='Delegation approval request created.', subtype_xmlid='mail.mt_note')
        return request

    def _approval_on_request_line_approved(self, line, approver):
        self.ensure_one()
        if not line:
            return
        values = {'approval_request_id': self.approval_request_id.id if self.approval_request_id else False}
        if line.sequence == 10:
            values.update({
                'original_approved_by_id': approver.id,
                'original_approved_on': fields.Datetime.now(),
            })
            self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write(values)
            self.message_post(body=f'Original approver approved via approval.request by {approver.name}.', subtype_xmlid='mail.mt_note')
            return
        if line.sequence >= 20:
            values.update({
                'delegated_accepted_by_id': approver.id,
                'delegated_accepted_on': fields.Datetime.now(),
            })
            self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write(values)
            self.message_post(body=f'Delegated approver approved via approval.request by {approver.name}.', subtype_xmlid='mail.mt_note')

    def _approval_on_request_line_rejected(self, line, approver, reason=''):
        self.ensure_one()
        if not line:
            return
        values = {}
        if line.sequence == 10:
            values.update({
                'state': 'rejected',
                'original_rejected_by_id': approver.id,
                'original_rejected_on': fields.Datetime.now(),
                'original_rejection_reason': reason or 'Rejected via approval.request.',
            })
        else:
            values.update({
                'state': 'rejected',
                'delegated_rejected_by_id': approver.id,
                'delegated_rejected_on': fields.Datetime.now(),
                'delegated_rejection_reason': reason or 'Rejected via approval.request.',
            })
        self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write(values)
        self.message_post(body=f'Delegation rejected via approval.request by {approver.name}.', subtype_xmlid='mail.mt_note')

    def _approval_on_request_cancelled(self, line, user):
        self.ensure_one()
        self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write({
            'state': 'cancelled',
            'cancelled_by_id': user.id,
            'cancelled_on': fields.Datetime.now(),
            'cancellation_reason': 'Cancelled via approval.request.',
        })
        self.message_post(body=f'Delegation cancelled via approval.request by {user.name}.', subtype_xmlid='mail.mt_note')

    def action_submit_for_approval(self):
        for record in self:
            if record.state != 'draft':
                raise UserError('Only draft delegations can be submitted for approval.')
            conflicts = record._check_date_conflict().sorted(lambda r: (r.date_from or fields.Date.today(), r.id))
            if conflicts:
                raise UserError(record._format_conflict_message(conflicts[:1]))
            record._approval_create_request()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approval Request',
            'res_model': 'approval.request',
            'view_mode': 'form',
            'res_id': self.approval_request_id.id if len(self) == 1 else False,
            'target': 'current',
        }

    def action_view_approval_request(self):
        self.ensure_one()
        if not self.approval_request_id:
            raise UserError('No approval request exists yet.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approval Request',
            'res_model': 'approval.request',
            'view_mode': 'form',
            'res_id': self.approval_request_id.id,
            'target': 'current',
        }

    def action_cancel(self):
        if not self._user_is_admin_or_officer():
            raise UserError('Only Approval Admin or Approval Delegation Officer can cancel delegations.')
        for record in self:
            if record.state in ('expired', 'cancelled'):
                raise UserError('Expired or cancelled delegations cannot be cancelled again.')
            record.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write({
                'state': 'cancelled',
                'cancelled_by_id': self.env.user.id,
                'cancelled_on': fields.Datetime.now(),
            })
            record.message_post(body='Delegation cancelled.', subtype_xmlid='mail.mt_note')
            if record.approval_request_id and record.approval_request_id.state not in ('cancelled', 'rejected', 'approved'):
                record.approval_request_id.with_context(approval_delegation_workflow_action=True).write({'state': 'cancelled'})
                record.approval_request_id._create_history('cancelled', 'Delegation was cancelled.', level_id=record.current_approval_level_id.id if record.current_approval_level_id else False)
        return True

    def action_set_draft(self):
        if not self._user_is_admin_or_officer():
            raise UserError('Only Approval Admin or Approval Delegation Officer can reset delegations to draft.')
        for record in self:
            if record.state not in ('rejected', 'cancelled'):
                raise UserError('Only rejected or cancelled delegations can be reset to draft.')
            record.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write({
                'state': 'draft',
                'approval_request_id': False,
                'current_approval_level_id': False,
                'submitted_by_id': False,
                'submitted_on': False,
            })
            record.message_post(body='Delegation reset to draft.', subtype_xmlid='mail.mt_note')
        return True

    def action_print(self):
        self.ensure_one()
        return self.env.ref('custom_approval_management.action_report_approval_delegation', raise_if_not_found=False).report_action(self)

    def _approval_on_final_approved(self):
        self.ensure_one()
        today = fields.Date.today()
        state = self._effective_state_from_dates(today)
        values = {
            'state': state,
            'original_approved_by_id': self.original_approved_by_id.id or self.env.user.id,
            'original_approved_on': self.original_approved_on or fields.Datetime.now(),
            'delegated_accepted_by_id': self.delegated_accepted_by_id.id or self.env.user.id,
            'delegated_accepted_on': self.delegated_accepted_on or fields.Datetime.now(),
        }
        if state == 'expired':
            values['expired_on'] = self.expired_on or fields.Datetime.now()
        self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write(values)
        self.message_post(body='Delegation approved through the approval request workflow.', subtype_xmlid='mail.mt_note')

    def _approval_on_rejected(self):
        self.ensure_one()
        self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write({
            'state': 'rejected',
            'original_rejected_by_id': self.env.user.id,
            'original_rejected_on': fields.Datetime.now(),
            'original_rejection_reason': 'Rejected through the approval request workflow.',
        })
        self.message_post(body='Delegation rejected through the approval request workflow.', subtype_xmlid='mail.mt_note')

    def _approval_on_cancelled(self):
        self.ensure_one()
        self.with_context(approval_delegation_workflow_action=True, skip_delegation_write_guard=True).write({
            'state': 'cancelled',
            'cancelled_by_id': self.env.user.id,
            'cancelled_on': fields.Datetime.now(),
            'cancellation_reason': 'Cancelled through the approval request workflow.',
        })
        self.message_post(body='Delegation cancelled through the approval request workflow.', subtype_xmlid='mail.mt_note')

    @api.model
    def _cron_update_delegation_states(self):
        delegations = self.sudo().search([('state', 'in', ['approved_pending_start', 'active', 'waiting_original_approval'])])
        delegations._sync_runtime_state(post_message=True)
        return True

    @api.model
    def _cron_expire_delegations(self):
        return self._cron_update_delegation_states()

    @api.model
    def _get_active_delegations_for_user(self, original_user, document_type=None, approval_level=None, company=None, date=None):
        today = date or fields.Date.today()
        original_user = self.env['res.users'].browse(original_user.id if hasattr(original_user, 'id') else original_user)
        if not original_user:
            return self.browse()
        domain = [
            ('original_user_id', '=', original_user.id),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_from', '<=', today),
            ('date_to', '>=', today),
            ('original_user_id.active', '=', True),
            ('delegated_user_id.active', '=', True),
        ]
        if company:
            company_id = company.id if hasattr(company, 'id') else company
            domain += ['|', ('company_id', '=', False), ('company_id', '=', company_id)]
        if document_type:
            document_type_id = document_type.id if hasattr(document_type, 'id') else document_type
            domain += ['|', ('document_type_id', '=', False), ('document_type_id', '=', document_type_id)]
        if approval_level:
            approval_level_id = approval_level.id if hasattr(approval_level, 'id') else approval_level
            domain += ['|', ('approval_level_id', '=', False), ('approval_level_id', '=', approval_level_id)]
        return self.sudo().search(domain).filtered(lambda d: d._delegation_is_currently_valid(today))

    @api.model
    def _get_delegated_original_users_for_user(self, current_user, document_type=None, approval_level=None, company=None, date=None):
        today = date or fields.Date.today()
        current_user = self.env['res.users'].browse(current_user.id if hasattr(current_user, 'id') else current_user)
        if not current_user:
            return self.env['res.users']
        domain = [
            ('delegated_user_id', '=', current_user.id),
            ('state', '=', 'active'),
            ('active', '=', True),
            ('date_from', '<=', today),
            ('date_to', '>=', today),
            ('original_user_id.active', '=', True),
            ('delegated_user_id.active', '=', True),
        ]
        if company:
            company_id = company.id if hasattr(company, 'id') else company
            domain += ['|', ('company_id', '=', False), ('company_id', '=', company_id)]
        if document_type:
            document_type_id = document_type.id if hasattr(document_type, 'id') else document_type
            domain += ['|', ('document_type_id', '=', False), ('document_type_id', '=', document_type_id)]
        if approval_level:
            approval_level_id = approval_level.id if hasattr(approval_level, 'id') else approval_level
            domain += ['|', ('approval_level_id', '=', False), ('approval_level_id', '=', approval_level_id)]
        return self.sudo().search(domain).filtered(lambda d: d._delegation_is_currently_valid(today)).mapped('original_user_id')

    def action_set_to_draft(self):
        return self.action_set_draft()
