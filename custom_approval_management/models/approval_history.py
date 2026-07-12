# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class ApprovalHistory(models.Model):
    _name = 'approval.history'
    _description = 'Approval History'
    _order = 'action_date DESC, id DESC'

    request_id = fields.Many2one(
        'approval.request',
        required=True,
        ondelete='cascade',
        string='Approval Request'
    )
    res_model = fields.Char(string='Document Model')
    res_id = fields.Integer(string='Document ID')
    level_id = fields.Many2one(
        'approval.level',
        string='Approval Level'
    )
    user_id = fields.Many2one(
        'res.users',
        default=lambda self: self.env.user,
        string='User'
    )
    action = fields.Selection(
        [
            ('submitted', 'Submitted'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('reset', 'Reset'),
            ('cancelled', 'Cancelled'),
        ],
        string='Action'
    )
    action_date = fields.Datetime(
        default=lambda self: fields.Datetime.now(),
        string='Action Date'
    )
    remarks = fields.Text(string='Remarks')
    is_delegated_action = fields.Boolean(default=False, string='Delegated Action')
    delegated_original_user_id = fields.Many2one('res.users', string='On Behalf Of')
    delegated_user_id = fields.Many2one('res.users', string='Delegated User')
    delegation_id = fields.Many2one('approval.delegation', string='Delegation')

    def unlink(self):
        raise UserError('Approval history records cannot be deleted.')
