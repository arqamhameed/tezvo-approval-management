# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseOrderApproval(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'approval.mixin']

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
    approval_request_id = fields.Many2one(copy=False)
    current_approval_level_id = fields.Many2one(copy=False)
    approval_required = fields.Boolean(
        string='Approval Required',
        compute='_compute_approval_required',
        store=False,
        copy=False,
    )

    @api.depends('amount_total', 'company_id')
    def _compute_approval_required(self):
        for order in self:
            order.approval_required = order._approval_required_for_document()

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
        return self.env['approval.document.type'].search([
            ('model_name', '=', 'purchase.order'),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
        ], order='company_id desc, id desc', limit=1)

    def _get_matching_approval_rule(self, document_type):
        self.ensure_one()
        if not document_type:
            return self.env['approval.rule']
        return self.env['approval.rule'].search([
            ('document_type_id', '=', document_type.id),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
            ('min_amount', '<=', self.amount_total),
            '|', ('max_amount', '=', 0), ('max_amount', '>=', self.amount_total),
        ], order='company_id desc, min_amount desc, id desc', limit=1)

    def _approval_required_for_document(self):
        self.ensure_one()
        doc_type = self._get_approval_document_type()
        if not doc_type:
            return False
        return bool(self._get_matching_approval_rule(doc_type))

    def button_confirm(self):
        """Override confirm to check approval before allowing confirmation."""
        self._check_approval_before_business_action()
        return super().button_confirm()

    def _approval_check_required(self):
        """Check if approval is required based on approval rules."""
        return self._approval_required_for_document()

    def action_submit_for_approval(self):
        self.ensure_one()
        if not self.id:
            raise UserError('Please save the Purchase Order before submitting for approval.')
        if not self._approval_required_for_document():
            raise UserError('No approval rule is configured for this Purchase Order.')
        if self.approval_request_id and self.approval_state in ('waiting', 'partial', 'approved'):
            raise UserError('An active approval request already exists for this Purchase Order.')
        return super().action_submit_for_approval()

    def _approval_get_document_type(self):
        """Get approval document type for purchase order."""
        return self._get_approval_document_type()

    def _approval_get_amount(self):
        """Get PO amount for approval."""
        return self.amount_total

    def _approval_get_partner(self):
        """Get PO partner."""
        return self.partner_id

    def _approval_get_document_ref(self):
        """Get PO reference."""
        return self.name

    def _approval_is_configured(self):
        """Check if approval is configured for this PO."""
        return self._approval_required_for_document() or self._get_approval_document_type()

    def _approval_cancel_linked_request(self, reason=''):
        """Cancel the linked approval request and update history. Keep request for audit trail."""
        self.ensure_one()
        request = self.approval_request_id
        if not request:
            return

        # Only update if not already in a terminal state
        if request.state not in ['cancelled', 'rejected', 'approved']:
            request.state = 'cancelled'
            # Set all pending lines to cancelled
            pending_lines = request.request_line_ids.filtered(lambda l: l.state == 'pending')
            pending_lines.write({
                'state': 'cancelled',
                'remarks': reason or 'Document was cancelled.',
            })
            # Create history record
            request._create_history('cancelled', reason or 'Document was cancelled.')

    def button_cancel(self):
        """Override cancel to handle approval state.
        
        Only restrict cancellation after business confirmation (purchase/done states).
        Allow cancellation in RFQ states (draft/sent) regardless of approval configuration.
        """
        for order in self:
            approval_configured = order._approval_is_configured()
            # Only restrict cancellation after business confirmation
            if (
                approval_configured
                and order.approval_state == 'approved'
                and order.state in ['purchase', 'done']
            ):
                document_type = order._approval_get_document_type()
                if document_type and not document_type.allow_cancel_approved_document:
                    raise UserError(
                        'This Purchase Order has already been approved and cannot be cancelled '
                        'based on the approval configuration.'
                    )
        
        result = super().button_cancel()
        
        for order in self:
            if order.approval_request_id:
                order._approval_cancel_linked_request('Purchase Order was cancelled.')
                order.write({
                    'approval_state': 'cancelled',
                    'current_approval_level_id': False,
                })
        
        return result

    def button_draft(self):
        """Override set to draft to handle approval reset.
        
        Cancels active approval request but keeps it for audit trail.
        Clears approval_request_id link so new submission creates new request.
        """
        result = super().button_draft()
        
        for order in self:
            # If there is an active approval request, cancel it but keep it for history
            if order.approval_request_id and order.approval_request_id.state not in ['cancelled', 'rejected', 'approved']:
                order._approval_cancel_linked_request('Purchase Order was reset to draft.')
            
            order.write({
                'approval_state': 'draft',
                'approval_request_id': False,
                'current_approval_level_id': False,
            })
        
        return result

    def _approval_create_request(self):
        """Create approval request for purchase order."""
        self.ensure_one()

        if not self.id:
            raise UserError(
                'Please save the Purchase Order before submitting for approval.'
            )

        doc_type = self._get_approval_document_type()
        if not doc_type:
            raise UserError('No approval rule is configured for this Purchase Order.')

        applicable_rule = self._get_matching_approval_rule(doc_type)

        if not applicable_rule:
            raise UserError('No approval rule is configured for this Purchase Order.')

        # Build request lines from rule lines
        request_lines = []
        for rule_line in applicable_rule._get_applicable_rule_lines(self.amount_total):
            request_lines.append((0, 0, {
                'sequence': rule_line.sequence,
                'level_id': rule_line.level_id.id,
                'minimum_user_count': rule_line.minimum_user_count,
                'mandatory': rule_line.mandatory,
                'allow_same_user': rule_line.allow_same_user,
                'notification_event_ids': [(6, 0, rule_line.notification_event_ids.ids)],
            }))

        # Calculate cycle number: count previous approval requests for same document
        previous_requests = self.env['approval.request'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', self.id),
        ])
        cycle_no = len(previous_requests) + 1

        # Create the approval request — res_model and res_id are mandatory fields
        approval_request = self.env['approval.request'].with_context(
            approval_request_internal_create=True
        ).create({
            'name': f'PO Approval: {self.name}',
            'document_type_id': doc_type.id,
            'res_model': 'purchase.order',
            'res_id': self.id,
            'document_ref': self.name or '/',
            'partner_id': self.partner_id.id if self.partner_id else False,
            'amount_total': self.amount_total,
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

        # Sync approval fields back to the PO
        self.approval_request_id = approval_request.id
        self.approval_state = 'waiting'
        self.current_approval_level_id = (
            approval_request.current_level_id.id
            if approval_request.current_level_id else False
        )

        return approval_request

    def _approval_on_final_approved(self):
        """Called when PO approval is finally approved."""
        self.approval_state = 'approved'
        self.current_approval_level_id = False

    def _approval_on_rejected(self):
        """Called when PO approval is rejected."""
        self.approval_state = 'rejected'
        self.current_approval_level_id = False

    def _approval_on_cancelled(self):
        """Called when approval request is cancelled."""
        self.approval_state = 'cancelled'
        self.current_approval_level_id = False

    def _approval_block_if_not_approved(self):
        """Block PO confirmation if not approved."""
        for order in self:
            if order._approval_required_for_document():
                if order.approval_state != 'approved':
                    raise UserError('This Purchase Order requires approval before confirmation.')

    @api.onchange('approval_request_id')
    def _onchange_approval_request(self):
        """Update PO approval state when approval request changes."""
        if self.approval_request_id:
            request = self.approval_request_id
            
            # Map approval.request states to PO approval states
            state_mapping = {
                'draft': 'draft',
                'waiting': 'waiting',
                'partial': 'partial',
                'approved': 'approved',
                'rejected': 'rejected',
                'cancelled': 'cancelled',
            }
            
            self.approval_state = state_mapping.get(request.state, request.state)
            self.current_approval_level_id = request.current_level_id
