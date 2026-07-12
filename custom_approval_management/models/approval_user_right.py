# -*- coding: utf-8 -*-
from odoo import api, fields, models, SUPERUSER_ID
from odoo.exceptions import ValidationError, UserError


class ApprovalUserRight(models.Model):
    _name = 'approval.user.right'
    _description = 'Approval User Right'
    _order = 'document_type_id, level_id, user_id'

    document_type_id = fields.Many2one(
        'approval.document.type',
        required=True,
        string='Document Type'
    )
    level_id = fields.Many2one(
        'approval.level',
        required=True,
        string='Approval Level'
    )
    user_id = fields.Many2one(
        'res.users',
        required=True,
        string='User'
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        string='Company'
    )
    value_lower_limit = fields.Float(default=0.0, string='Value Lower Limit')
    value_upper_limit = fields.Float(default=0.0, string='Value Upper Limit')
    expiry_date = fields.Date(string='Expiry Date')
    mandatory = fields.Boolean(default=False, string='Mandatory')
    document_read = fields.Boolean(default=True, string='Can Read Document')
    active = fields.Boolean(default=True)

    @api.constrains('value_lower_limit', 'value_upper_limit')
    def _check_value_range(self):
        for record in self:
            if record.value_upper_limit > 0 and record.value_upper_limit < record.value_lower_limit:
                raise ValidationError(
                    'Value Upper Limit must be 0 or greater than/equal to Value Lower Limit'
                )

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
