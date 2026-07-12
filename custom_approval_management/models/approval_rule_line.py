# -*- coding: utf-8 -*-
from odoo import api, fields, models, SUPERUSER_ID
from odoo.exceptions import ValidationError, UserError


class ApprovalRuleLine(models.Model):
    _name = 'approval.rule.line'
    _description = 'Approval Rule Line'
    _order = 'sequence, id'

    rule_id = fields.Many2one(
        'approval.rule',
        required=True,
        ondelete='cascade',
        string='Approval Rule'
    )
    sequence = fields.Integer(default=10, string='Sequence')
    level_id = fields.Many2one(
        'approval.level',
        required=True,
        string='Approval Level'
    )
    minimum_user_count = fields.Integer(default=1, string='Minimum User Count')
    mandatory = fields.Boolean(default=True, string='Mandatory')
    min_amount = fields.Float(default=0.0, string='Min Amount')
    max_amount = fields.Float(default=0.0, string='Max Amount', help='0 means unlimited.')
    notification_event_ids = fields.Many2many(
        'approval.notification.event',
        'approval_rule_line_notification_event_rel',
        'rule_line_id',
        'event_id',
        string='Notification Events',
    )
    value_lower_limit = fields.Float(default=0.0, string='Value Lower Limit')
    value_upper_limit = fields.Float(default=0.0, string='Value Upper Limit')
    allow_same_user = fields.Boolean(default=False, string='Allow Same User')
    active = fields.Boolean(default=True)

    @api.constrains('minimum_user_count')
    def _check_minimum_user_count(self):
        for record in self:
            if record.minimum_user_count <= 0:
                raise ValidationError(
                    'Minimum User Count must be greater than 0'
                )

    @api.constrains('min_amount', 'max_amount')
    def _check_amount_range(self):
        for record in self:
            if record.min_amount < 0:
                raise ValidationError('Min Amount must be 0 or greater.')
            if record.max_amount > 0 and record.max_amount < record.min_amount:
                raise ValidationError(
                    'Max Amount must be 0 or greater than/equal to Min Amount'
                )

    @api.constrains('value_lower_limit', 'value_upper_limit')
    def _check_value_range(self):
        for record in self:
            if record.value_upper_limit > 0 and record.value_upper_limit < record.value_lower_limit:
                raise ValidationError(
                    'Value Upper Limit must be 0 or greater than/equal to Value Lower Limit'
                )

    def _matches_amount(self, amount):
        self.ensure_one()
        if amount < self.min_amount:
            return False
        if self.max_amount > 0 and amount > self.max_amount:
            return False
        return True

    def _is_internal_approval_operation(self):
        """Check if this operation is part of internal approval setup, sync, or module loading.
        
        Returns True for:
        - Internal approval setup operations (approval_internal_setup_apply)
        - Internal approval sync operations (approval_internal_sync)
        - Module install/update/data loading context
        - Superuser during module operations
        """
        ctx = self.env.context
        # Allow internal approval operations
        if ctx.get('approval_internal_setup_apply') or ctx.get('approval_internal_sync'):
            return True
        # Allow during module install/update/loading
        if ctx.get('install_mode') or ctx.get('update_module') or ctx.get('module'):
            return True
        # Allow superuser during module operations (additional safety)
        if self.env.uid == SUPERUSER_ID and (ctx.get('module') or ctx.get('update_module')):
            return True
        return False

    def create(self, vals_list):
        # Backend protection: block manual creation unless internal flag is present
        if not self._is_internal_approval_operation():
            raise UserError('Manual creation is not allowed for Back Rules and Setup records. Please use Approval Setup.')
        return super().create(vals_list)

    def write(self, vals):
        # Backend protection: block manual write unless user is in special group or internal flag is present
        if not self.env.context.get('approval_internal_setup_apply') and not self.env.context.get('approval_internal_sync'):
            if not self.env.user.has_group('custom_approval_management.group_approval_back_rules_editor'):
                from odoo.exceptions import UserError
                raise UserError('You do not have permission to edit Back Rules and Setup records. Please contact an Approval Back Rules Editor.')
        return super().write(vals)

    def unlink(self):
        # Backend protection: block manual deletion
        if not self.env.context.get('approval_internal_setup_apply') and not self.env.context.get('approval_internal_sync'):
            from odoo.exceptions import UserError
            raise UserError('Deletion is not allowed for Back Rules and Setup records. Please archive or deactivate where applicable.')
        return super().unlink()

    def copy(self, default=None):
        # Backend protection: block manual duplication
        from odoo.exceptions import UserError
        raise UserError('Duplicate is not allowed for Back Rules and Setup records.')
