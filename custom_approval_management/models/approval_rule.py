# -*- coding: utf-8 -*-
from odoo import api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, ValidationError


class ApprovalRule(models.Model):
    _name = 'approval.rule'
    _description = 'Approval Rule'
    _order = 'name'

    name = fields.Char(required=True, string='Rule Name')
    document_type_id = fields.Many2one(
        'approval.document.type',
        required=True,
        string='Document Type'
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        string='Company'
    )
    min_amount = fields.Float(default=0.0, string='Minimum Amount')
    max_amount = fields.Float(default=0.0, string='Maximum Amount')
    rule_line_ids = fields.One2many(
        'approval.rule.line',
        'rule_id',
        string='Approval Levels'
    )
    active = fields.Boolean(default=True)

    @api.constrains('min_amount', 'max_amount')
    def _check_amount_range(self):
        for record in self:
            if record.max_amount > 0 and record.max_amount < record.min_amount:
                raise ValidationError(
                    'Maximum Amount must be 0 or greater than/equal to Minimum Amount'
                )

    def _get_applicable_rule_lines(self, amount):
        self.ensure_one()
        lines = self.rule_line_ids.filtered(
            lambda line: line.active and line._matches_amount(amount)
        ).sorted('sequence')
        if not lines:
            raise UserError('No applicable approval level is configured for this document amount.')
        return lines

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
                raise UserError('You do not have permission to edit Back Rules and Setup records. Please contact an Approval Back Rules Editor.')
        return super().write(vals)

    def unlink(self):
        # Backend protection: block manual deletion
        if not self.env.context.get('approval_internal_setup_apply') and not self.env.context.get('approval_internal_sync'):
            raise UserError('Deletion is not allowed for Back Rules and Setup records. Please archive or deactivate where applicable.')
        return super().unlink()

    def copy(self, default=None):
        # Backend protection: block manual duplication
        raise UserError('Duplicate is not allowed for Back Rules and Setup records.')
