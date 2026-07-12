# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class AccountPaymentApproval(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment', 'approval.mixin']

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

    @api.depends('amount', 'company_id')
    def _compute_approval_required(self):
        for payment in self:
            payment.approval_required = payment._approval_required_for_document()

    def _approval_get_account_payment_nature(self):
        """Return (module_code, label) for supported payment approval natures."""
        self.ensure_one()
        if self.payment_type == 'outbound' and self.partner_type == 'supplier':
            return 'vendor_payment', 'Vendor Payment'
        if self.payment_type == 'inbound' and self.partner_type == 'customer':
            return 'customer_payment', 'Customer Payment'
        return False, False

    def _is_vendor_payment_document(self):
        self.ensure_one()
        module_code, _label = self._approval_get_account_payment_nature()
        return module_code == 'vendor_payment'

    def _is_customer_payment_document(self):
        self.ensure_one()
        module_code, _label = self._approval_get_account_payment_nature()
        return module_code == 'customer_payment'

    def _is_supported_payment_document(self):
        self.ensure_one()
        module_code, _label = self._approval_get_account_payment_nature()
        return bool(module_code)

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
        module_code, _label = self._approval_get_account_payment_nature()
        if not module_code:
            return self.env['approval.document.type']

        candidates = self.env['approval.document.type'].search([
            ('model_name', '=', 'account.payment'),
            ('module_code', '=', module_code),
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
        return self.env['approval.rule'].search([
            ('document_type_id', '=', document_type.id),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
            ('min_amount', '<=', self.amount),
            '|', ('max_amount', '=', 0), ('max_amount', '>=', self.amount),
        ], order='company_id desc, min_amount desc, id desc', limit=1)

    def _approval_required_for_document(self):
        self.ensure_one()
        if not self._is_supported_payment_document():
            return False
        doc_type = self._get_approval_document_type()
        if not doc_type:
            return False
        return bool(self._get_matching_approval_rule(doc_type))

    def _approval_is_configured(self):
        self.ensure_one()
        if not self._is_supported_payment_document():
            return False
        return bool(self._approval_required_for_document() or self._get_approval_document_type())

    def _approval_check_required(self):
        return self._approval_required_for_document()

    def _approval_get_document_type(self):
        return self._get_approval_document_type()

    def _approval_get_amount(self):
        return self.amount

    def _approval_get_partner(self):
        return self.partner_id

    def _approval_get_document_ref(self):
        return self.name or self.ref or '/'

    def action_submit_for_approval(self):
        self.ensure_one()
        _module_code, document_label = self._approval_get_account_payment_nature()
        if not document_label:
            raise UserError('Submit for Approval is available only for Vendor Payments and Customer Payments.')
        if self.state != 'draft':
            raise UserError(f'Only draft {document_label}s can be submitted for approval.')
        if not self.id:
            raise UserError(f'Please save the {document_label} before submitting for approval.')
        if not self._approval_required_for_document():
            raise UserError(f'No approval rule is configured for this {document_label}.')
        if self.approval_request_id and self.approval_request_id.state in ('draft', 'waiting', 'partial'):
            raise UserError(f'An active approval request already exists for this {document_label}.')
        return super().action_submit_for_approval()

    def action_post(self):
        self._check_approval_before_business_action()
        return super().action_post()

    def action_validate(self):
        self._check_approval_before_business_action()
        return super().action_validate()

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
        for payment in self:
            if (
                payment._approval_is_configured()
                and payment.approval_state == 'approved'
                and payment.state == 'posted'
            ):
                document_type = payment._approval_get_document_type()
                if document_type and not document_type.allow_cancel_approved_document:
                    _module_code, document_label = payment._approval_get_account_payment_nature()
                    raise UserError(
                        f'This {document_label} has already been approved and cannot be cancelled '
                        'based on the approval configuration.'
                    )

        result = super().action_cancel()

        for payment in self:
            if payment.approval_request_id:
                _module_code, document_label = payment._approval_get_account_payment_nature()
                if document_label:
                    payment._approval_cancel_linked_request(f'{document_label} was cancelled.')
                else:
                    payment._approval_cancel_linked_request('Payment was cancelled.')
                payment.write({
                    'approval_state': 'cancelled',
                    'current_approval_level_id': False,
                })

        return result

    def action_draft(self):
        result = super().action_draft()

        for payment in self:
            if payment.approval_request_id and payment.approval_request_id.state not in ['cancelled', 'rejected', 'approved']:
                _module_code, document_label = payment._approval_get_account_payment_nature()
                if document_label:
                    payment._approval_cancel_linked_request(f'{document_label} was reset to draft.')
                else:
                    payment._approval_cancel_linked_request('Payment was reset to draft.')
            payment.write({
                'approval_state': 'draft',
                'approval_request_id': False,
                'current_approval_level_id': False,
            })

        return result

    def _approval_create_request(self):
        self.ensure_one()

        _module_code, document_label = self._approval_get_account_payment_nature()
        if not document_label:
            raise UserError('Approval is supported only for Vendor Payments and Customer Payments.')

        if not self.id:
            raise UserError(f'Please save the {document_label} before submitting for approval.')

        doc_type = self._get_approval_document_type()
        if not doc_type:
            raise UserError(f'No approval rule is configured for this {document_label}.')

        applicable_rule = self._get_matching_approval_rule(doc_type)
        if not applicable_rule:
            raise UserError(f'No approval rule is configured for this {document_label}.')

        request_lines = []
        for rule_line in applicable_rule._get_applicable_rule_lines(self.amount):
            request_lines.append((0, 0, {
                'sequence': rule_line.sequence,
                'level_id': rule_line.level_id.id,
                'minimum_user_count': rule_line.minimum_user_count,
                'mandatory': rule_line.mandatory,
                'allow_same_user': rule_line.allow_same_user,
                'notification_event_ids': [(6, 0, rule_line.notification_event_ids.ids)],
            }))

        previous_requests = self.env['approval.request'].search([
            ('res_model', '=', 'account.payment'),
            ('res_id', '=', self.id),
        ])
        cycle_no = len(previous_requests) + 1

        approval_request = self.env['approval.request'].with_context(
            approval_request_internal_create=True
        ).create({
            'name': f'{document_label} Approval: {self.name or self.ref or "/"}',
            'document_type_id': doc_type.id,
            'res_model': 'account.payment',
            'res_id': self.id,
            'document_ref': self.name or self.ref or '/',
            'partner_id': self.partner_id.id if self.partner_id else False,
            'amount_total': self.amount,
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
