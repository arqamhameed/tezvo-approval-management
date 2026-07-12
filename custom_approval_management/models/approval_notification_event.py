# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import ValidationError


class ApprovalNotificationEvent(models.Model):
    _name = 'approval.notification.event'
    _description = 'Approval Notification Event'
    _order = 'sequence, id'

    name = fields.Char(required=True, string='Name')
    code = fields.Selection(
        [
            ('submit', 'Submit'),
            ('next_level', 'Next Level'),
            ('final_approval', 'Final Approval'),
            ('rejection', 'Rejection'),
            ('cancellation', 'Cancellation'),
        ],
        required=True,
        string='Code',
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ('approval_notification_event_code_uniq', 'unique(code)', 'Notification event code must be unique.'),
    ]

    def write(self, vals):
        protected_codes = {'submit', 'next_level', 'final_approval', 'rejection', 'cancellation'}
        for rec in self:
            if rec.code in protected_codes and 'code' in vals and vals['code'] != rec.code:
                raise ValidationError('Default notification event codes cannot be changed.')
        return super().write(vals)
