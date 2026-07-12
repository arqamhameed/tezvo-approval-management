# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.fields import Command


class ResUsersApproval(models.Model):
    """Extend res.users with convenience Boolean fields for approval group membership.

    These fields allow the Settings > Users form to show a dedicated
    "Approval Rights" section that is visible without developer mode,
    without relying on res.groups category_id (unsupported in Odoo 19).
    """
    _inherit = 'res.users'

    approval_group_user = fields.Boolean(
        string='Approval User',
        compute='_compute_approval_groups',
        inverse='_inverse_approval_group_user',
        help='Member of the Approval User group: can access My Pending Approvals and approve or reject currently assigned requests.',
    )
    approval_group_manager = fields.Boolean(
        string='Approval Manager',
        compute='_compute_approval_groups',
        inverse='_inverse_approval_group_manager',
        help='Member of the Approval Manager group: can monitor approval requests and history, and can act on assigned approvals.',
    )
    approval_group_admin = fields.Boolean(
        string='Approval Admin',
        compute='_compute_approval_groups',
        inverse='_inverse_approval_group_admin',
        help='Member of the Approval Admin group: full administrative access to approval configuration and monitoring.',
    )
    approval_group_delegation_officer = fields.Boolean(
        string='Approval Delegation Officer',
        compute='_compute_approval_groups',
        inverse='_inverse_approval_group_delegation_officer',
        help='Member of the Approval Delegation Officer group: can amend approved/active delegation records.',
    )
    approval_group_back_rules_editor = fields.Boolean(
        string='Approval Back Rules Editor',
        compute='_compute_approval_groups',
        inverse='_inverse_approval_group_back_rules_editor',
        help='Member of the Approval Back Rules Editor group: can manually edit Back Rules and Setup records (Document Types, Approval Rules, Approval Users).',
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _approval_group_ref(self, xml_id):
        """Return the res.groups record for the given local XML ID, or False."""
        return self.env.ref(
            'custom_approval_management.{}'.format(xml_id),
            raise_if_not_found=False,
        )

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    @api.depends('group_ids')
    def _compute_approval_groups(self):
        group_user = self._approval_group_ref('group_custom_approval_user')
        group_manager = self._approval_group_ref('group_custom_approval_manager')
        group_admin = self._approval_group_ref('group_custom_approval_admin')
        group_delegation_officer = self._approval_group_ref('group_custom_approval_delegation_officer')
        group_back_rules_editor = self._approval_group_ref('group_approval_back_rules_editor')
        for user in self:
            gids = user.group_ids.ids
            user.approval_group_user = bool(group_user and group_user.id in gids)
            user.approval_group_manager = bool(group_manager and group_manager.id in gids)
            user.approval_group_admin = bool(group_admin and group_admin.id in gids)
            user.approval_group_delegation_officer = bool(
                group_delegation_officer and group_delegation_officer.id in gids
            )
            user.approval_group_back_rules_editor = bool(
                group_back_rules_editor and group_back_rules_editor.id in gids
            )

    # ------------------------------------------------------------------
    # Inverses
    # ------------------------------------------------------------------

    def _inverse_approval_group_user(self):
        self._set_approval_group(
            'group_custom_approval_user',
            lambda u: u.approval_group_user,
        )

    def _inverse_approval_group_manager(self):
        self._set_approval_group(
            'group_custom_approval_manager',
            lambda u: u.approval_group_manager,
        )

    def _inverse_approval_group_admin(self):
        self._set_approval_group(
            'group_custom_approval_admin',
            lambda u: u.approval_group_admin,
        )

    def _inverse_approval_group_delegation_officer(self):
        self._set_approval_group(
            'group_custom_approval_delegation_officer',
            lambda u: u.approval_group_delegation_officer,
        )

    def _inverse_approval_group_back_rules_editor(self):
        self._set_approval_group(
            'group_approval_back_rules_editor',
            lambda u: u.approval_group_back_rules_editor,
        )

    def _set_approval_group(self, xml_id, flag_getter):
        """Add or remove a single approval group for each user in self."""
        group = self._approval_group_ref(xml_id)
        if not group:
            return
        for user in self:
            if flag_getter(user):
                user.sudo().write({'group_ids': [Command.link(group.id)]})
            else:
                user.sudo().write({'group_ids': [Command.unlink(group.id)]})
