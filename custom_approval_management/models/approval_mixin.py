# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class ApprovalMixin(models.AbstractModel):
    _name = 'approval.mixin'
    _description = 'Approval Mixin'

    approval_state = fields.Selection(
        [
            ('no_approval', 'No Approval Required'),
            ('draft', 'Draft'),
            ('waiting', 'Waiting Approval'),
            ('partial', 'Partially Approved'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='draft',
        string='Approval State'
    )
    approval_request_id = fields.Many2one(
        'approval.request',
        string='Approval Request'
    )
    approval_required = fields.Boolean(default=False, string='Approval Required')
    current_approval_level_id = fields.Many2one(
        'approval.level',
        string='Current Approval Level'
    )
    approval_history_count = fields.Integer(
        compute='_compute_approval_history_count',
        string='Approval History Count'
    )

    def _compute_approval_history_count(self):
        for record in self:
            if record.approval_request_id:
                record.approval_history_count = len(record.approval_request_id.history_ids)
            else:
                record.approval_history_count = 0

    def action_submit_for_approval(self):
        """Submit document for approval."""
        self.ensure_one()
        if not self.id:
            raise UserError(
                'Please save the document before submitting for approval.'
            )
        approval_request = self._approval_create_request()
        if approval_request:
            # _approval_create_request is responsible for writing back to self;
            # these writes are safe as a fallback if a subclass does not do it.
            if not self.approval_request_id:
                self.approval_request_id = approval_request.id
            if self.approval_state not in ('waiting', 'partial', 'approved'):
                self.approval_state = 'waiting'
            if not self.current_approval_level_id and approval_request.current_level_id:
                self.current_approval_level_id = approval_request.current_level_id.id
            return {
                'type': 'ir.actions.act_window',
                'name': 'Approval Request',
                'res_model': 'approval.request',
                'res_id': approval_request.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def action_approve_document(self):
        """Approve the document (wrapper for approval request)."""
        self.ensure_one()
        if self.approval_request_id:
            return self.approval_request_id.action_approve()

    def action_open_reject_wizard(self):
        """Open reject wizard."""
        self.ensure_one()
        if self.approval_request_id:
            return self.approval_request_id.action_open_reject_wizard()

    def action_reset_approval(self):
        """Reset approval process."""
        self.approval_state = 'draft'
        if self.approval_request_id:
            self.approval_request_id.state = 'draft'

    def action_view_approval_request(self):
        """View approval request."""
        self.ensure_one()
        if self.approval_request_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Approval Request',
                'res_model': 'approval.request',
                'res_id': self.approval_request_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def _approval_get_document_type(self):
        """Get approval document type. Override in inheriting model."""
        return None

    def _approval_get_amount(self):
        """Get document amount for approval. Override in inheriting model."""
        return 0.0

    def _approval_get_partner(self):
        """Get document partner. Override in inheriting model."""
        return None

    def _approval_get_document_ref(self):
        """Get document reference. Override in inheriting model."""
        return ''

    def _approval_check_required(self):
        """Check if approval is required. Override in inheriting model."""
        return False

    def _approval_create_request(self):
        """Create approval request. Override in inheriting model."""
        return None

    def _approval_on_final_approved(self):
        """Called when approval is finally approved. Override in inheriting model."""
        pass

    def _approval_on_rejected(self):
        """Called when approval is rejected. Override in inheriting model."""
        pass

    def _approval_block_if_not_approved(self):
        """Block document action if not approved. Override in inheriting model."""
        pass

    def _check_approval_before_business_action(self):
        """Block business actions when approval is required but not approved."""
        for record in self:
            checker = getattr(record, '_approval_required_for_document', None)
            if not checker or not checker():
                continue

            approval_state = getattr(record, 'approval_state', False) or 'draft'
            if approval_state == 'approved':
                continue

            if approval_state in ('waiting', 'waiting_approval', 'partial'):
                raise UserError('This document is waiting for approval.')

            if approval_state == 'rejected':
                raise UserError('This document was rejected. Please revise and resubmit for approval.')

            raise UserError(
                'This document requires approval. Please submit it for approval before proceeding.'
            )
