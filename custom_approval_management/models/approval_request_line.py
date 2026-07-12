# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ApprovalRequestLine(models.Model):
    _name = 'approval.request.line'
    _description = 'Approval Request Line'
    _order = 'sequence, id'

    request_id = fields.Many2one(
        'approval.request',
        required=True,
        ondelete='cascade',
        string='Approval Request'
    )
    sequence = fields.Integer(default=10, string='Sequence')
    level_id = fields.Many2one(
        'approval.level',
        required=True,
        string='Approval Level'
    )
    minimum_user_count = fields.Integer(default=1, string='Minimum Approvers')
    required_user_ids = fields.Many2many(
        'res.users',
        'approval_request_line_user_rel',
        'line_id',
        'user_id',
        string='Required Users'
    )
    approved_user_ids = fields.Many2many(
        'res.users',
        'approval_request_line_approved_rel',
        'line_id',
        'user_id',
        string='Approved By'
    )
    rejected_user_id = fields.Many2one(
        'res.users',
        string='Rejected By'
    )
    state = fields.Selection(
        [
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('skipped', 'Skipped'),
            ('cancelled', 'Cancelled'),
        ],
        default='pending',
        string='Status'
    )
    approved_date = fields.Datetime(string='Approved Date')
    rejected_date = fields.Datetime(string='Rejected Date')
    remarks = fields.Text(string='Remarks')
    allow_same_user = fields.Boolean(default=False, string='Allow Same User')
    mandatory = fields.Boolean(default=True, string='Mandatory')
    notification_event_ids = fields.Many2many(
        'approval.notification.event',
        'approval_request_line_notification_event_rel',
        'request_line_id',
        'event_id',
        string='Notification Events',
    )
    allowed_approver_names = fields.Char(
        compute='_compute_allowed_approver_names',
        string='Allowed Approvers',
    )
    is_delegated_action = fields.Boolean(default=False, string='Delegated Action')
    delegated_original_user_id = fields.Many2one('res.users', string='On Behalf Of')
    delegated_user_id = fields.Many2one('res.users', string='Delegated Approver')
    delegation_id = fields.Many2one('approval.delegation', string='Delegation')

    @api.constrains('minimum_user_count')
    def _check_minimum_user_count(self):
        for record in self:
            if record.minimum_user_count <= 0:
                raise ValidationError('Minimum User Count must be greater than 0')

    @api.depends(
        'request_id.document_type_id',
        'request_id.company_id',
        'request_id.amount_total',
        'level_id',
        'required_user_ids',
    )
    def _compute_allowed_approver_names(self):
        for line in self:
            line.allowed_approver_names = ''
            if line.required_user_ids:
                line.allowed_approver_names = ', '.join(line.required_user_ids.filtered(lambda u: u.active).mapped('name'))
                continue

            if not line.request_id or not line.request_id.document_type_id or not line.level_id:
                continue

            rights = self.env['approval.user.right'].search([
                ('document_type_id', '=', line.request_id.document_type_id.id),
                ('level_id', '=', line.level_id.id),
                ('active', '=', True),
                '|', ('company_id', '=', False), ('company_id', '=', line.request_id.company_id.id),
            ])

            today = fields.Date.today()
            allowed_users = self.env['res.users']
            for right in rights:
                if right.expiry_date and right.expiry_date < today:
                    continue
                if line.request_id.amount_total < right.value_lower_limit:
                    continue
                if right.value_upper_limit > 0 and line.request_id.amount_total > right.value_upper_limit:
                    continue
                allowed_users |= right.user_id

            line.allowed_approver_names = ', '.join(allowed_users.mapped('name'))
