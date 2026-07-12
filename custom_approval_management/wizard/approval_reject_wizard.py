# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class ApprovalRejectWizard(models.TransientModel):
    _name = 'approval.reject.wizard'
    _description = 'Approval Reject Wizard'

    approval_request_id = fields.Many2one(
        'approval.request',
        required=True,
        string='Approval Request'
    )
    reason = fields.Text(
        required=True,
        string='Reason for Rejection'
    )

    def action_reject(self):
        """Reject the approval request."""
        self.ensure_one()
        if not self.approval_request_id:
            raise UserError('Approval Request not found.')
        
        self.approval_request_id.action_reject(reason=self.reason)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approval Request',
            'res_model': 'approval.request',
            'res_id': self.approval_request_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
