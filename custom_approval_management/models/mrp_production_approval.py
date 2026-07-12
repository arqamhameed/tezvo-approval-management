# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class MrpProductionApproval(models.Model):
    _name = 'mrp.production'
    _inherit = ['mrp.production', 'approval.mixin']

    approval_state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('waiting', 'Waiting Approval'),
            ('partial', 'Partially Approved'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        copy=False,
        default='draft'
    )
    approval_request_id = fields.Many2one('approval.request', string='Approval Request', copy=False)
    current_approval_level_id = fields.Many2one('approval.level', string='Current Approval Level', copy=False)
    approval_required = fields.Boolean(
        string='Approval Required',
        compute='_compute_approval_required',
        store=False,
        copy=False,
    )

    @api.depends('company_id')
    def _compute_approval_required(self):
        for production in self:
            production.approval_required = production._approval_required_for_document()

    def copy(self, default=None):
        default = dict(default or {})
        default.update({
            'approval_state': 'draft',
            'approval_request_id': False,
            'current_approval_level_id': False,
        })
        return super().copy(default)

    def _get_approval_document_type(self):
        self.ensure_one()
        candidates = self.env['approval.document.type'].search([
            ('model_name', '=', 'mrp.production'),
            ('module_code', '=', 'mrp_production'),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
        ], order='company_id desc, id desc')
        for doc_type in candidates:
            if doc_type.matches_record(self):
                return doc_type
        return self.env['approval.document.type']

    def _get_matching_approval_rule(self, document_type):
        self.ensure_one()
        if not document_type:
            return self.env['approval.rule']
        amount = 0.0
        return self.env['approval.rule'].search([
            ('document_type_id', '=', document_type.id),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
            ('min_amount', '<=', amount),
            '|', ('max_amount', '=', 0), ('max_amount', '>=', amount),
        ], order='company_id desc, min_amount desc, id desc', limit=1)

    def _approval_required_for_document(self):
        self.ensure_one()
        doc_type = self._get_approval_document_type()
        if not doc_type:
            return False
        return bool(self._get_matching_approval_rule(doc_type))

    def _approval_is_configured(self):
        self.ensure_one()
        return bool(self._approval_required_for_document() or self._get_approval_document_type())

    def _approval_check_required(self):
        return self._approval_required_for_document()

    def _approval_get_document_type(self):
        return self._get_approval_document_type()

    def _approval_get_amount(self):
        return 0.0

    def _approval_get_partner(self):
        return False

    def _approval_get_document_ref(self):
        return self.name or self.origin or '/'

    def action_submit_for_approval(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('Only draft Manufacturing Orders can be submitted for approval.')
        if not self.id:
            raise UserError('Please save the Manufacturing Order before submitting for approval.')
        if not self._approval_required_for_document():
            raise UserError('No approval rule is configured for this Manufacturing Order.')
        if self.approval_request_id and self.approval_request_id.state in ('draft', 'waiting', 'partial'):
            raise UserError('An active approval request already exists for this Manufacturing Order.')
        return super().action_submit_for_approval()

    def action_confirm(self):
        self._check_approval_before_business_action()
        return super().action_confirm()

    def button_mark_done(self):
        self._check_approval_before_business_action()
        return super().button_mark_done()

    def _approval_cancel_linked_request(self, reason=''):
        self.ensure_one()
        request = self.approval_request_id
        if not request:
            return
        if request.state not in ['cancelled', 'rejected', 'approved']:
            request.state = 'cancelled'
            pending_lines = request.request_line_ids.filtered(lambda l: l.state == 'pending')
            pending_lines.write({
                'state': 'cancelled',
                'remarks': reason or 'Document was cancelled.',
            })
            request._create_history('cancelled', reason or 'Document was cancelled.')

    def action_cancel(self):
        for production in self:
            if (
                production._approval_is_configured()
                and production.approval_state == 'approved'
                and production.state in ['confirmed', 'progress', 'to_close', 'done']
            ):
                document_type = production._approval_get_document_type()
                if document_type and not document_type.allow_cancel_approved_document:
                    raise UserError(
                        'This Manufacturing Order has already been approved and cannot be cancelled '
                        'based on the approval configuration.'
                    )

        result = super().action_cancel()

        for production in self:
            if production.approval_request_id:
                production._approval_cancel_linked_request('Manufacturing Order was cancelled.')
                production.write({
                    'approval_state': 'cancelled',
                    'current_approval_level_id': False,
                })

        return result

    def _approval_create_request(self):
        self.ensure_one()

        if not self.id:
            raise UserError('Please save the Manufacturing Order before submitting for approval.')

        doc_type = self._get_approval_document_type()
        if not doc_type:
            raise UserError('No approval rule is configured for this Manufacturing Order.')

        applicable_rule = self._get_matching_approval_rule(doc_type)
        if not applicable_rule:
            raise UserError('No approval rule is configured for this Manufacturing Order.')

        request_lines = []
        for rule_line in applicable_rule._get_applicable_rule_lines(self._approval_get_amount()):
            request_lines.append((0, 0, {
                'sequence': rule_line.sequence,
                'level_id': rule_line.level_id.id,
                'minimum_user_count': rule_line.minimum_user_count,
                'mandatory': rule_line.mandatory,
                'allow_same_user': rule_line.allow_same_user,
                'notification_event_ids': [(6, 0, rule_line.notification_event_ids.ids)],
            }))

        previous_requests = self.env['approval.request'].search([
            ('res_model', '=', 'mrp.production'),
            ('res_id', '=', self.id),
        ])
        cycle_no = len(previous_requests) + 1

        approval_request = self.env['approval.request'].with_context(
            approval_request_internal_create=True
        ).create({
            'name': f'Manufacturing Order Approval: {self.name or self.origin or "/"}',
            'document_type_id': doc_type.id,
            'res_model': 'mrp.production',
            'res_id': self.id,
            'document_ref': self.name or self.origin or '/',
            'partner_id': False,
            'amount_total': 0.0,
            'submitted_by': self.env.user.id,
            'submitted_date': fields.Datetime.now(),
            'company_id': self.company_id.id,
            'state': 'waiting',
            'cycle_no': cycle_no,
            'request_line_ids': request_lines,
        })

        first_line = approval_request.request_line_ids.sorted('sequence')[:1]
        if first_line:
            approval_request.current_level_id = first_line.level_id.id

        self.approval_request_id = approval_request.id
        self.approval_state = 'waiting'
        self.current_approval_level_id = approval_request.current_level_id.id if approval_request.current_level_id else False

        return approval_request

    def _approval_on_final_approved(self):
        self.approval_state = 'approved'
        self.current_approval_level_id = False

    def _approval_on_rejected(self):
        self.approval_state = 'rejected'
        self.current_approval_level_id = False

    def _approval_on_cancelled(self):
        self.approval_state = 'cancelled'
        self.current_approval_level_id = False
