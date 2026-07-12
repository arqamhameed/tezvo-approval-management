# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup, escape


_DOCUMENT_NATURE_SELECTION = [
    ('purchase_order', 'Purchase Order'),
    ('vendor_bill', 'Vendor Bill'),
    ('vendor_credit_note', 'Vendor Credit Note'),
    ('customer_invoice', 'Customer Invoice'),
    ('customer_credit_note', 'Customer Credit Note'),
    ('account_journal_entry', 'Journal Entry'),
    ('vendor_payment', 'Vendor Payment'),
    ('customer_payment', 'Customer Payment'),
    ('stock_receipt', 'Stock Receipt / GRN'),
    ('delivery_order', 'Delivery Order / GI'),
    ('internal_transfer', 'Internal Transfer'),
    ('sales_order', 'Sales Order'),
    ('mrp_production', 'Manufacturing Order'),
    ('mrp_unbuild', 'Unbuild Order'),
    ('stock_scrap', 'Scrap Order'),
    ('approval_delegation', 'Approval Delegation'),
    ('custom', 'Custom / Advanced'),
]

_MODULE_TO_NATURES = {
    'purchase': ['purchase_order'],
    'sale_management': ['sales_order'],
    'sale': ['sales_order'],
    'account': [
        'vendor_bill',
        'vendor_credit_note',
        'customer_invoice',
        'customer_credit_note',
        'vendor_payment',
        'customer_payment',
        'account_journal_entry',
    ],
    'stock': ['stock_receipt', 'delivery_order', 'internal_transfer', 'stock_scrap'],
    'mrp': ['mrp_production', 'mrp_unbuild'],
    'custom_approval_management': ['approval_delegation'],
}

_NATURE_TO_MODULE_CODES = {}
for _module_code, _natures in _MODULE_TO_NATURES.items():
    for _nature in _natures:
        _NATURE_TO_MODULE_CODES.setdefault(_nature, []).append(_module_code)

_APPROVAL_MODULE_SELECTION = [
    ('purchase', 'Purchase'),
    ('sale', 'Sales'),
    ('account', 'Accounting'),
    ('stock', 'Inventory'),
    ('mrp', 'Manufacturing'),
    ('custom_approval_management', 'Approval Management'),
]

_APPROVAL_MODULE_LABELS = dict(_APPROVAL_MODULE_SELECTION)

_APPROVAL_MODULE_INSTALL_CHECK = {
    'purchase': ['purchase'],
    'sale': ['sale_management', 'sale'],
    'account': ['account'],
    'stock': ['stock'],
    'mrp': ['mrp'],
    'custom_approval_management': ['custom_approval_management'],
}

_APPROVAL_MODULE_TO_NATURES = {
    'purchase': ['purchase_order'],
    'sale': ['sales_order'],
    'account': [
        'vendor_bill',
        'vendor_credit_note',
        'customer_invoice',
        'customer_credit_note',
        'vendor_payment',
        'customer_payment',
        'account_journal_entry',
    ],
    'stock': ['stock_receipt', 'delivery_order', 'internal_transfer', 'stock_scrap'],
    'mrp': ['mrp_production', 'mrp_unbuild'],
    'custom_approval_management': ['approval_delegation'],
}

_NATURE_TO_APPROVAL_MODULE = {
    nature: module_code
    for module_code, natures in _APPROVAL_MODULE_TO_NATURES.items()
    for nature in natures
}


class ApprovalSetupConfigLine(models.Model):
    """One approval level step inside an Approval Setup record.

    Mirrors approval.rule.line + the approver / user-right fields so the
    administrator never has to visit separate backend screens.
    """
    _name = 'approval.setup.config.line'
    _description = 'Approval Setup Line'
    _order = 'sequence, id'

    @api.model
    def _default_notification_event_ids(self):
        events = self.env['approval.notification.event'].search([
            ('code', 'in', ['submit', 'next_level']),
            ('active', '=', True),
        ])
        return events.ids

    setup_id = fields.Many2one(
        'approval.setup.config',
        required=True,
        ondelete='cascade',
        string='Approval Setup',
    )
    sequence = fields.Integer(default=10, string='Sequence')
    approval_level_id = fields.Many2one(
        'approval.level',
        required=True,
        string='Approval Level',
    )
    minimum_user_count = fields.Integer(default=1, string='Minimum Approvers')
    mandatory = fields.Boolean(default=True, string='Mandatory')
    allow_same_user = fields.Boolean(default=False, string='Allow Submitter to Approve')
    min_amount = fields.Float(default=0.0, string='Min Amount')
    max_amount = fields.Float(default=0.0, string='Max Amount', help='0 means unlimited.')
    notification_event_ids = fields.Many2many(
        'approval.notification.event',
        'approval_setup_line_notification_event_rel',
        'line_id',
        'event_id',
        string='Notification Events',
        default=_default_notification_event_ids,
    )
    approver_user_ids = fields.Many2many(
        'res.users',
        'approval_setup_line_user_rel',
        'line_id',
        'user_id',
        string='Approvers',
    )
    value_lower_limit = fields.Float(default=0.0, string='Value Lower Limit')
    value_upper_limit = fields.Float(default=0.0, string='Value Upper Limit')
    expiry_date = fields.Date(string='Expiry Date')
    can_read_document = fields.Boolean(default=True, string='Can Read Document')
    active = fields.Boolean(default=True)

    # Back-links to generated backend records (filled during Apply)
    rule_line_id = fields.Many2one(
        'approval.rule.line',
        string='Rule Line',
        readonly=True,
        copy=False,
    )

    @api.constrains('minimum_user_count')
    def _check_minimum_user_count(self):
        for rec in self:
            if rec.minimum_user_count <= 0:
                raise ValidationError('Minimum Approvers must be greater than 0.')

    @api.constrains('value_lower_limit', 'value_upper_limit')
    def _check_value_range(self):
        for rec in self:
            if rec.value_upper_limit > 0 and rec.value_upper_limit < rec.value_lower_limit:
                raise ValidationError(
                    'Value Upper Limit must be 0 or greater than/equal to Value Lower Limit.'
                )

    @api.constrains('min_amount', 'max_amount')
    def _check_amount_range(self):
        for rec in self:
            if rec.min_amount < 0:
                raise ValidationError('Min Amount must be 0 or greater.')
            if rec.max_amount > 0 and rec.max_amount < rec.min_amount:
                raise ValidationError(
                    'Max Amount must be 0 or greater than or equal to Min Amount.'
                )

    def _mark_parent_pending(self):
        if (
            self.env.context.get('approval_setup_apply_in_progress')
            or self.env.context.get('approval_internal_setup_apply')
            or self.env.context.get('approval_skip_change_tracking')
            or self.env.context.get('approval_internal_sync')
        ):
            return
        for line in self:
            setup = line.setup_id
            if setup and setup.setup_state == 'applied':
                setup.with_context(approval_setup_apply_in_progress=True).write({
                    'setup_state': 'changes_pending',
                    'apply_error_message': 'This setup has unapplied changes. Click Apply Approval Setup to update the approval rules.',
                })
                if not self.env.context.get('approval_setup_suppress_audit_logs'):
                    setup._post_pending_after_apply_note()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            min_amount = vals.get('min_amount')
            max_amount = vals.get('max_amount')
            if min_amount is not None:
                vals['value_lower_limit'] = min_amount
            if max_amount is not None:
                vals['value_upper_limit'] = max_amount
        records = super().create(vals_list)
        if not (
            self.env.context.get('approval_setup_apply_in_progress')
            or self.env.context.get('approval_setup_suppress_audit_logs')
            or self.env.context.get('install_mode')
            or self.env.context.get('module_uninstall')
        ):
            for line in records:
                if line.setup_id:
                    line.setup_id._post_setup_line_added(line)
        records._mark_parent_pending()
        return records

    def write(self, vals):
        snapshots = {line.id: line._audit_snapshot() for line in self}
        vals = dict(vals)
        if 'min_amount' in vals:
            vals['value_lower_limit'] = vals['min_amount']
        if 'max_amount' in vals:
            vals['value_upper_limit'] = vals['max_amount']
        result = super().write(vals)
        if not (
            self.env.context.get('approval_setup_apply_in_progress')
            or self.env.context.get('approval_setup_suppress_audit_logs')
            or self.env.context.get('install_mode')
            or self.env.context.get('module_uninstall')
        ):
            for line in self:
                if line.setup_id:
                    line.setup_id._post_setup_line_updated(line, snapshots.get(line.id, {}))
        self._mark_parent_pending()
        return result

    def unlink(self):
        removed = [(line.setup_id.id, line._audit_snapshot()) for line in self if line.setup_id]
        setups = self.mapped('setup_id')
        result = super().unlink()
        if not (
            self.env.context.get('approval_setup_apply_in_progress')
            or self.env.context.get('approval_setup_suppress_audit_logs')
            or self.env.context.get('install_mode')
            or self.env.context.get('module_uninstall')
        ):
            setup_model = self.env['approval.setup.config']
            for setup_id, snapshot in removed:
                setup = setup_model.browse(setup_id).exists()
                if setup:
                    setup._post_setup_line_removed(snapshot)
        for setup in setups:
            if setup and setup.setup_state == 'applied':
                setup.with_context(approval_setup_apply_in_progress=True).write({
                    'setup_state': 'changes_pending',
                    'apply_error_message': 'This setup has unapplied changes. Click Apply Approval Setup to update the approval rules.',
                })
                if not self.env.context.get('approval_setup_suppress_audit_logs'):
                    setup._post_pending_after_apply_note()
        return result

    def _audit_snapshot(self):
        self.ensure_one()
        return {
            'approval_level': self.approval_level_id.display_name or '-',
            'min_amount': self.min_amount,
            'max_amount': self.max_amount,
            'minimum_user_count': self.minimum_user_count,
            'mandatory': self.mandatory,
            'allow_same_user': self.allow_same_user,
            'notification_events': ', '.join(self.notification_event_ids.mapped('name')) or '-',
            'approvers': ', '.join(self.approver_user_ids.mapped('name')) or '-',
            'rule_line': self.rule_line_id.display_name or '-',
            'value_lower_limit': self.value_lower_limit,
            'value_upper_limit': self.value_upper_limit,
            'can_read_document': self.can_read_document,
            'expiry_date': fields.Date.to_string(self.expiry_date) if self.expiry_date else '-',
            'active': self.active,
        }


class ApprovalSetupConfig(models.Model):
    """Single-screen setup for one complete approval configuration.

    Automatically creates / updates:
      - approval.document.type
      - approval.rule (+ approval.rule.line)
      - approval.user.right
    """
    _name = 'approval.setup.config'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Approval Setup'
    _order = 'name'

    name = fields.Char(required=True, string='Setup Name')
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        string='Company',
    )
    module_id = fields.Many2one(
        'ir.module.module',
        string='Technical Module',
        required=False,
        ondelete='cascade',
        domain=[
            ('state', '=', 'installed'),
            ('name', 'in', ['purchase', 'sale_management', 'sale', 'account', 'stock', 'mrp', 'custom_approval_management']),
        ],
        help='Internal technical module mapping used by setup logic.',
    )
    approval_module_code = fields.Selection(
        selection='_selection_installed_supported_modules',
        string='Module Code (Legacy)',
        required=False,
        help='Shows only supported business modules that are installed.',
    )
    approval_integration_module_id = fields.Many2one(
        'approval.integration.module',
        string='Module',
        required=True,
        domain="[('is_available', '=', True), ('active', '=', True)]",
    )
    document_nature = fields.Selection(
        selection=_DOCUMENT_NATURE_SELECTION,
        required=False,
        default=False,
        string='Document Type',
    )
    allow_cancel_approved_document = fields.Boolean(
        default=False,
        string='Allow Cancellation After Approval',
    )
    notify_on_submit = fields.Boolean(
        default=True,
        string='Notify On Submit',
        help='If enabled, Odoo will send email notifications for this approval event.',
    )
    notify_on_next_level = fields.Boolean(
        default=True,
        string='Notify On Next Level',
        help='If enabled, Odoo will send email notifications for this approval event.',
    )
    notify_on_final_approval = fields.Boolean(
        default=True,
        string='Notify On Final Approval',
        help='If enabled, Odoo will send email notifications for this approval event.',
    )
    notify_on_rejection = fields.Boolean(
        default=True,
        string='Notify On Rejection',
        help='If enabled, Odoo will send email notifications for this approval event.',
    )
    notify_on_cancellation = fields.Boolean(
        default=False,
        string='Notify On Cancellation',
        help='If enabled, Odoo will send email notifications for this approval event.',
    )
    active = fields.Boolean(default=True)
    setup_state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('changes_pending', 'Changes Pending'),
            ('applied', 'Applied'),
        ],
        default='draft',
        readonly=True,
        copy=False,
        string='Setup Status',
    )
    last_applied_by_id = fields.Many2one('res.users', readonly=True, copy=False, string='Last Applied By')
    last_applied_on = fields.Datetime(readonly=True, copy=False, string='Last Applied On')
    apply_error_message = fields.Text(readonly=True, copy=False, string='Apply Error')

    # Amount range for the generated rule
    min_amount = fields.Float(default=0.0, string='Minimum Amount')
    max_amount = fields.Float(
        default=0.0,
        string='Maximum Amount',
        help='0 means unlimited.',
    )

    # Setup lines (one per approval level)
    setup_line_ids = fields.One2many(
        'approval.setup.config.line',
        'setup_id',
        string='Approval Levels',
    )

    # Back-links to generated technical records
    document_type_id = fields.Many2one(
        'approval.document.type',
        string='Approval Document Type',
        required=True,
        copy=False,
        domain="[('integration_module_id', '=', approval_integration_module_id), ('is_available', '=', True), ('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    approval_rule_id = fields.Many2one(
        'approval.rule',
        string='Approval Rule',
        readonly=True,
        copy=False,
    )

    # Computed counters for smart buttons
    user_right_count = fields.Integer(
        compute='_compute_user_right_count',
        string='Approver Rights',
    )
    rule_line_count = fields.Integer(
        compute='_compute_rule_line_count',
        string='Rule Lines',
    )

    # Computed hint: natures already configured for the selected company
    taken_natures_hint = fields.Char(
        compute='_compute_taken_natures_hint',
        string='Already Configured',
    )

    _PENDING_TRIGGER_FIELDS = {
        'approval_integration_module_id',
        'approval_module_code',
        'document_nature',
        'document_type_id',
        'module_id',
        'company_id',
        'allow_cancel_approved_document',
        'notify_on_submit',
        'notify_on_next_level',
        'notify_on_final_approval',
        'notify_on_rejection',
        'notify_on_cancellation',
        'active',
        'min_amount',
        'max_amount',
        'setup_line_ids',
    }

    # ----------------------------------------------------------------
    # Duplicate-nature prevention
    # ----------------------------------------------------------------

    def _auto_init(self):
        result = super()._auto_init()
        try:
            self.env['approval.integration.module'].sudo().sync_integrations()
            to_backfill = self.sudo().search(['|', ('module_id', '=', False), '|', ('approval_integration_module_id', '=', False), '|', ('approval_module_code', '=', False), ('document_type_id', '=', False)])
            for setup in to_backfill:
                vals = {}
                document_type = setup.document_type_id
                if not document_type and setup.document_nature:
                    document_type = self.env['approval.document.type'].sudo().search([
                        ('document_nature', '=', setup.document_nature),
                        '|', ('company_id', '=', setup.company_id.id), ('company_id', '=', False),
                    ], limit=1)
                if document_type and not setup.document_type_id:
                    vals['document_type_id'] = document_type.id
                if document_type and not setup.document_nature:
                    vals['document_nature'] = document_type.document_nature
                if not vals.get('document_nature') and setup.document_nature:
                    vals['document_nature'] = setup.document_nature

                module_code = setup.approval_module_code or (document_type.approval_module_code if document_type else False)
                if not module_code and vals.get('document_nature'):
                    module_code = _NATURE_TO_APPROVAL_MODULE.get(vals['document_nature'])
                if module_code and not setup.approval_module_code:
                    vals['approval_module_code'] = module_code
                integration = setup.approval_integration_module_id or (document_type.integration_module_id if document_type else False)
                if not integration and module_code:
                    integration = self.env['approval.integration.module'].sudo().search([('code', '=', module_code)], limit=1)
                if integration and not setup.approval_integration_module_id:
                    vals['approval_integration_module_id'] = integration.id

                module = self._get_preferred_installed_module_for_code(module_code)
                if module:
                    vals['module_id'] = module.id
                if vals:
                    setup.with_context(approval_setup_apply_in_progress=True, approval_setup_suppress_audit_logs=True).write(vals)
        except Exception:
            pass
        return result

    @api.model
    def _selection_installed_supported_modules(self):
        integration_model = self.env['approval.integration.module'].sudo()
        integration_model.sync_integrations()
        integrations = integration_model.search([
            ('active', '=', True),
            ('is_available', '=', True),
        ], order='sequence, name')
        return [(rec.code, rec.name) for rec in integrations if rec.code]

    @api.model
    def _is_module_installed(self, module_name):
        if not module_name:
            return False
        return bool(self.env['ir.module.module'].sudo().search_count([
            ('name', '=', module_name),
            ('state', '=', 'installed'),
        ]))

    @api.model
    def _allowed_natures_for_module_code(self, module_code):
        if not module_code:
            return set()
        document_types = self.env['approval.document.type'].sudo().search([
            ('approval_module_code', '=', module_code),
            ('active', '=', True),
        ])
        return set(document_types.mapped('document_nature'))

    def _get_preferred_installed_module_for_code(self, module_code):
        if not module_code:
            return self.env['ir.module.module']
        module_model = self.env['ir.module.module'].sudo()
        integration = self.env['approval.integration.module'].sudo().search([
            ('code', '=', module_code),
        ], limit=1)
        doc_type = self.env['approval.document.type'].sudo().search([
            ('integration_module_id', '=', integration.id),
            ('active', '=', True),
        ], limit=1) if integration else self.env['approval.document.type']
        candidates = [
            integration.target_module_names.split(',')[0].strip() if integration and integration.target_module_names else False,
            integration.connector_module_name if integration else False,
            doc_type.target_module_name if doc_type else False,
            doc_type.connector_module_name if doc_type else False,
            module_code,
        ]
        for technical_name in [name for name in candidates if name]:
            module = module_model.search([('name', '=', technical_name), ('state', '=', 'installed')], limit=1)
            if module:
                return module
        return self.env['ir.module.module']

    def _get_preferred_module_for_nature(self, nature):
        module_code = _NATURE_TO_APPROVAL_MODULE.get(nature)
        return self._get_preferred_installed_module_for_code(module_code)

    def action_sync_approval_integrations(self):
        self.env['approval.integration.module'].sudo().sync_integrations()
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    @api.onchange('approval_integration_module_id')
    def _onchange_approval_integration_module_id(self):
        if not self.approval_integration_module_id:
            self.approval_module_code = False
            self.module_id = False
            self.document_type_id = False
            self.document_nature = False
            return {'domain': {'document_type_id': []}}

        integration = self.approval_integration_module_id
        self.approval_module_code = integration.code
        module = self._get_preferred_installed_module_for_code(integration.code)
        self.module_id = module.id if module else False
        if self.document_type_id and self.document_type_id.integration_module_id != integration:
            self.document_type_id = False
            self.document_nature = False

        return {
            'domain': {
                'document_type_id': [
                    ('integration_module_id', '=', integration.id),
                    ('is_available', '=', True),
                    ('active', '=', True),
                    '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
                ]
            }
        }

    @api.onchange('approval_module_code')
    def _onchange_approval_module_code(self):
        if self.approval_module_code and not self.approval_integration_module_id:
            integration = self.env['approval.integration.module'].sudo().search([
                ('code', '=', self.approval_module_code),
            ], limit=1)
            self.approval_integration_module_id = integration.id if integration else False
        module = self._get_preferred_installed_module_for_code(self.approval_module_code)
        self.module_id = module.id if module else False

        if not self.approval_module_code:
            self.document_type_id = False
            self.document_nature = False
            return {'domain': {'document_type_id': []}}

        domain = [
            ('integration_module_id', '=', self.approval_integration_module_id.id),
            ('is_available', '=', True),
            ('approval_module_code', '=', self.approval_module_code),
            ('active', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ]
        if self.document_type_id and self.document_type_id.approval_module_code != self.approval_module_code:
            self.document_type_id = False
            self.document_nature = False
        return {'domain': {'document_type_id': domain}}

    @api.onchange('document_type_id')
    def _onchange_document_type_id(self):
        if not self.document_type_id:
            self.document_nature = False
            return
        self.document_nature = self.document_type_id.document_nature
        self.approval_module_code = self.document_type_id.approval_module_code
        self.approval_integration_module_id = self.document_type_id.integration_module_id
        module = self._get_preferred_installed_module_for_code(self.approval_module_code)
        self.module_id = module.id if module else False

    @api.constrains('approval_integration_module_id', 'approval_module_code', 'document_type_id', 'module_id', 'document_nature')
    def _check_module_document_type_consistency(self):
        for rec in self:
            if not rec.approval_integration_module_id:
                raise ValidationError('Module is required.')
            if not rec.approval_integration_module_id.is_available:
                raise ValidationError(
                    'The selected approval module is no longer available because its related Odoo module '
                    'or approval connector is not installed.'
                )
            if rec.approval_integration_module_id.code and rec.approval_module_code != rec.approval_integration_module_id.code:
                raise ValidationError('Module mapping is inconsistent. Please reselect the Module.')
            if not rec.document_type_id:
                raise ValidationError('Document Type is required.')
            module = rec._get_preferred_installed_module_for_code(rec.approval_module_code)
            if not module:
                raise ValidationError('The selected Module is not installed.')
            if rec.document_type_id.integration_module_id != rec.approval_integration_module_id:
                raise ValidationError(
                    'The selected Document Type does not belong to the selected Module. '
                    'Please select a valid Document Type.'
                )
            if rec.document_type_id.approval_module_code != rec.approval_module_code:
                raise ValidationError(
                    'The selected Document Type does not belong to the selected Module. '
                    'Please select a valid Document Type.'
                )
            if rec.document_nature != rec.document_type_id.document_nature:
                raise ValidationError(
                    'Document Type mapping is inconsistent. Please reselect the Document Type.'
                )

    @api.constrains('document_nature', 'company_id')
    def _check_duplicate_nature_setup(self):
        """Prevent two setup records with the same non-custom nature + company."""
        for rec in self:
            if not rec.document_nature or rec.document_nature == 'custom':
                continue
            duplicate = self.env['approval.setup.config'].search([
                ('id', '!=', rec.id),
                ('document_nature', '=', rec.document_nature),
                ('company_id', '=', rec.company_id.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    f'An Approval Setup for Document Type "{rec.document_nature}" '
                    f'already exists for company "{rec.company_id.name}" '
                    f'(setup: "{duplicate.name}"). '
                    'Please edit the existing setup or choose a different Document Type.'
                )

    @api.onchange('document_nature', 'company_id', 'approval_module_code', 'document_type_id')
    def _onchange_warn_duplicate_nature(self):
        """Immediately warn the user when they select a nature already in use."""
        if not self.approval_module_code:
            self.document_nature = False
            return
        if self.document_type_id and self.document_type_id.approval_module_code != self.approval_module_code:
            self.document_type_id = False
            self.document_nature = False
            return {
                'warning': {
                    'title': 'Module / Document Type Mismatch',
                    'message': (
                        'The selected Document Type does not belong to the selected Module. '
                        'Please select a valid Document Type.'
                    ),
                }
            }
        if self.document_type_id:
            self.document_nature = self.document_type_id.document_nature
        if self.document_nature:
            allowed = self._allowed_natures_for_module_code(self.approval_module_code)
            if self.document_nature not in allowed:
                self.document_nature = False
                return {
                    'warning': {
                        'title': 'Module / Document Type Mismatch',
                        'message': (
                            'The selected Document Type does not belong to the selected Module. '
                            'Please select a valid Document Type.'
                        ),
                    }
                }
        if not self.document_nature or self.document_nature == 'custom':
            return
        if not self.company_id:
            return
        duplicate = self.env['approval.setup.config'].search([
            ('id', '!=', self._origin.id or 0),
            ('document_nature', '=', self.document_nature),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if duplicate:
            return {
                'warning': {
                    'title': 'Document Type Already Configured',
                    'message': (
                        f'An Approval Setup named "{duplicate.name}" already exists '
                        f'for this Document Type and company. '
                        'Saving will be blocked. '
                        'Please edit the existing setup or choose a different Document Type.'
                    ),
                }
            }

    @api.depends('company_id')
    def _compute_taken_natures_hint(self):
        """Build a human-readable list of natures already configured for the company."""
        _LABELS = dict(self._fields['document_nature'].selection
                       if isinstance(self._fields['document_nature'].selection, list)
                       else self._fields['document_nature']._description_selection(self.env))
        for rec in self:
            if not rec.company_id:
                rec.taken_natures_hint = ''
                continue
            taken = self.env['approval.setup.config'].search([
                ('id', '!=', rec._origin.id or 0),
                ('company_id', '=', rec.company_id.id),
                ('document_nature', '!=', False),
                ('document_nature', '!=', 'custom'),
            ]).mapped('document_nature')
            if taken:
                labels = [_LABELS.get(n, n) for n in taken]
                rec.taken_natures_hint = 'Already configured for this company: ' + ', '.join(labels)
            else:
                rec.taken_natures_hint = ''

    @api.constrains('min_amount', 'max_amount')
    def _check_amounts(self):
        for rec in self:
            if rec.max_amount > 0 and rec.max_amount < rec.min_amount:
                raise ValidationError(
                    'Maximum Amount must be 0 (unlimited) or ≥ Minimum Amount.'
                )

    def _audit_field_label(self, field_name):
        labels = {
            'name': 'Setup Name',
            'approval_integration_module_id': 'Module',
            'approval_module_code': 'Module',
            'module_id': 'Module',
            'document_nature': 'Document Type',
            'document_type_id': 'Approval Document Type',
            'company_id': 'Company',
            'min_amount': 'Minimum Amount',
            'max_amount': 'Maximum Amount',
            'allow_cancel_approved_document': 'Allow Cancellation After Approval',
            'active': 'Active',
            'notify_on_submit': 'Notify On Submit',
            'notify_on_next_level': 'Notify On Next Level',
            'notify_on_final_approval': 'Notify On Final Approval',
            'notify_on_rejection': 'Notify On Rejection',
            'notify_on_cancellation': 'Notify On Cancellation',
            'setup_state': 'Setup Status',
            'last_applied_by_id': 'Last Applied By',
            'last_applied_on': 'Last Applied On',
            'apply_error_message': 'Apply Error',
        }
        return labels.get(field_name, field_name)

    def _format_audit_field_value(self, field_name, value):
        self.ensure_one()
        if value in (False, None, ''):
            return '-'
        field = self._fields.get(field_name)
        if not field:
            return str(value)
        if field.type == 'boolean':
            return 'Yes' if value else 'No'
        if field.type == 'many2one':
            record = value if hasattr(value, 'display_name') else self.env[field.comodel_name].browse(value)
            return record.display_name or '-'
        if field.type == 'many2many':
            records = value if hasattr(value, 'mapped') else self.env[field.comodel_name].browse(value)
            names = records.mapped('display_name')
            return ', '.join(names) if names else '-'
        if field.type == 'selection':
            selection = dict(field.selection)
            return selection.get(value, value)
        if field.type in ('float', 'monetary'):
            return f'{float(value):,.2f}'
        if field.type == 'datetime':
            return fields.Datetime.to_string(value)
        if field.type == 'date':
            return fields.Date.to_string(value)
        return str(value)

    def _post_audit_table(self, title, rows, subtitle=''):
        self.ensure_one()
        if not rows:
            return
        safe_title = escape(title)
        safe_user = escape(self.env.user.name or '-')
        safe_changed_on = escape(fields.Datetime.to_string(fields.Datetime.now()) or '-')
        safe_setup = escape(self.display_name or '-')
        header = (
            f'<p><strong>{safe_title}</strong></p>'
            f'<p>Changed By: {safe_user}<br/>'
            f'Changed On: {safe_changed_on}<br/>'
            f'Setup: {safe_setup}</p>'
        )
        if subtitle:
            header += f'<p>{escape(subtitle)}</p>'
        table_rows = ''.join(
            '<tr>'
            f'<td style="border:1px solid #ddd; padding:6px;">{escape(label)}</td>'
            f'<td style="border:1px solid #ddd; padding:6px;">{escape(old)}</td>'
            f'<td style="border:1px solid #ddd; padding:6px;">{escape(new)}</td>'
            '</tr>'
            for label, old, new in rows
        )
        body = (
            f'{header}'
            '<table style="width:100%; border-collapse:collapse; font-size:13px;">'
            '<thead><tr>'
            '<th style="background:#f3f2f7; border:1px solid #ddd; padding:6px; text-align:left;">Field</th>'
            '<th style="background:#f3f2f7; border:1px solid #ddd; padding:6px; text-align:left;">Old Value</th>'
            '<th style="background:#f3f2f7; border:1px solid #ddd; padding:6px; text-align:left;">New Value</th>'
            '</tr></thead>'
            f'<tbody>{table_rows}</tbody>'
            '</table>'
        )
        self.message_post(body=Markup(body), message_type='comment', subtype_xmlid='mail.mt_note')

    def _post_pending_after_apply_note(self):
        self.ensure_one()
        self.message_post(
            body=Markup(
                '<p><strong>Approval Setup Changed After Apply</strong></p>'
                '<p>This setup has unapplied changes. Please click Apply Approval Setup to update approval rules.</p>'
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def _post_setup_line_added(self, line):
        self.ensure_one()
        snapshot = line._audit_snapshot()
        rows = [
            ('Approval Level', '-', snapshot['approval_level']),
            ('Min Amount', '-', f"{float(snapshot['min_amount']):,.2f}"),
            ('Max Amount', '-', f"{float(snapshot['max_amount']):,.2f}"),
            ('Minimum Approvers', '-', str(snapshot['minimum_user_count'])),
            ('Mandatory', '-', 'Yes' if snapshot['mandatory'] else 'No'),
            ('Allow Submitter Approval', '-', 'Yes' if snapshot['allow_same_user'] else 'No'),
            ('Notification Events', '-', snapshot['notification_events']),
            ('Approvers', '-', snapshot['approvers']),
            ('Rule Line', '-', snapshot['rule_line']),
        ]
        self._post_audit_table('Approval Level Added', rows)

    def _post_setup_line_updated(self, line, old_snapshot):
        self.ensure_one()
        new_snapshot = line._audit_snapshot()
        field_map = [
            ('Approval Level', 'approval_level'),
            ('Min Amount', 'min_amount'),
            ('Max Amount', 'max_amount'),
            ('Minimum Approvers', 'minimum_user_count'),
            ('Mandatory', 'mandatory'),
            ('Allow Submitter Approval', 'allow_same_user'),
            ('Notification Events', 'notification_events'),
            ('Approvers', 'approvers'),
            ('Rule Line', 'rule_line'),
            ('Value From', 'value_lower_limit'),
            ('Value To', 'value_upper_limit'),
            ('Can Read Document', 'can_read_document'),
            ('Expiry Date', 'expiry_date'),
            ('Active', 'active'),
        ]

        def _fmt(key, val):
            if key in ('mandatory', 'allow_same_user', 'can_read_document', 'active'):
                return 'Yes' if val else 'No'
            if key in ('min_amount', 'max_amount', 'value_lower_limit', 'value_upper_limit'):
                return f'{float(val or 0):,.2f}'
            return str(val) if val not in (False, None, '') else '-'

        rows = []
        for label, key in field_map:
            old_text = _fmt(key, old_snapshot.get(key))
            new_text = _fmt(key, new_snapshot.get(key))
            if old_text != new_text:
                rows.append((label, old_text, new_text))

        if rows:
            subtitle = f'Approval Level Updated: {new_snapshot.get("approval_level", "-")}'
            self._post_audit_table('Approval Level Updated', rows, subtitle=subtitle)

    def _post_setup_line_removed(self, snapshot):
        self.ensure_one()
        rows = [
            ('Approval Level', snapshot.get('approval_level', '-'), '-'),
            ('Min Amount', f"{float(snapshot.get('min_amount') or 0):,.2f}", '-'),
            ('Max Amount', f"{float(snapshot.get('max_amount') or 0):,.2f}", '-'),
            ('Minimum Approvers', str(snapshot.get('minimum_user_count', '-')), '-'),
            ('Mandatory', 'Yes' if snapshot.get('mandatory') else 'No', '-'),
            ('Allow Submitter Approval', 'Yes' if snapshot.get('allow_same_user') else 'No', '-'),
            ('Notification Events', snapshot.get('notification_events', '-'), '-'),
            ('Approvers', snapshot.get('approvers', '-'), '-'),
            ('Rule Line', snapshot.get('rule_line', '-'), '-'),
        ]
        self._post_audit_table('Approval Level Removed', rows)

    def _mark_changes_pending(self):
        for rec in self:
            if rec.setup_state == 'applied':
                rec.setup_state = 'changes_pending'
                rec.apply_error_message = False
                if not self.env.context.get('approval_setup_suppress_audit_logs'):
                    rec._post_pending_after_apply_note()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('document_type_id') and vals.get('document_nature'):
                document_type = self.env['approval.document.type'].sudo().search([
                    ('document_nature', '=', vals['document_nature']),
                    '|', ('company_id', '=', vals.get('company_id') or self.env.company.id), ('company_id', '=', False),
                ], limit=1)
                if document_type:
                    vals['document_type_id'] = document_type.id
            if vals.get('document_type_id') and not vals.get('document_nature'):
                document_type = self.env['approval.document.type'].sudo().browse(vals['document_type_id']).exists()
                if document_type:
                    vals['document_nature'] = document_type.document_nature
                    vals['approval_module_code'] = vals.get('approval_module_code') or document_type.approval_module_code
                    vals['approval_integration_module_id'] = vals.get('approval_integration_module_id') or document_type.integration_module_id.id
            if vals.get('approval_integration_module_id') and not vals.get('approval_module_code'):
                integration = self.env['approval.integration.module'].sudo().browse(vals['approval_integration_module_id']).exists()
                if integration:
                    vals['approval_module_code'] = integration.code
            if not vals.get('approval_module_code') and vals.get('document_nature'):
                vals['approval_module_code'] = _NATURE_TO_APPROVAL_MODULE.get(vals['document_nature'])
            if not vals.get('approval_integration_module_id') and vals.get('approval_module_code'):
                integration = self.env['approval.integration.module'].sudo().search([('code', '=', vals['approval_module_code'])], limit=1)
                if integration:
                    vals['approval_integration_module_id'] = integration.id
            if not vals.get('module_id') and vals.get('approval_module_code'):
                module = self._get_preferred_installed_module_for_code(vals['approval_module_code'])
                if module:
                    vals['module_id'] = module.id
        records = super().create(vals_list)
        return records

    def write(self, vals):
        vals = dict(vals)
        if vals.get('document_type_id') and not vals.get('document_nature'):
            document_type = self.env['approval.document.type'].sudo().browse(vals['document_type_id']).exists()
            if document_type:
                vals['document_nature'] = document_type.document_nature
                vals['approval_module_code'] = vals.get('approval_module_code') or document_type.approval_module_code
                vals['approval_integration_module_id'] = vals.get('approval_integration_module_id') or document_type.integration_module_id.id
        if vals.get('approval_integration_module_id') and not vals.get('approval_module_code'):
            integration = self.env['approval.integration.module'].sudo().browse(vals['approval_integration_module_id']).exists()
            if integration:
                vals['approval_module_code'] = integration.code
        if not vals.get('document_type_id') and vals.get('document_nature'):
            document_type = self.env['approval.document.type'].sudo().search([
                ('document_nature', '=', vals['document_nature']),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
            ], limit=1)
            if document_type:
                vals['document_type_id'] = document_type.id
                vals['approval_module_code'] = vals.get('approval_module_code') or document_type.approval_module_code
                vals['approval_integration_module_id'] = vals.get('approval_integration_module_id') or document_type.integration_module_id.id
        if not vals.get('approval_module_code') and vals.get('document_nature'):
            vals['approval_module_code'] = _NATURE_TO_APPROVAL_MODULE.get(vals['document_nature'])
        if not vals.get('approval_integration_module_id') and vals.get('approval_module_code'):
            integration = self.env['approval.integration.module'].sudo().search([('code', '=', vals['approval_module_code'])], limit=1)
            if integration:
                vals['approval_integration_module_id'] = integration.id
        if not vals.get('module_id') and vals.get('approval_module_code'):
            module = self._get_preferred_installed_module_for_code(vals['approval_module_code'])
            if module:
                vals['module_id'] = module.id
        tracked_fields = {
            'name',
            'approval_integration_module_id',
            'approval_module_code',
            'module_id',
            'document_nature',
            'document_type_id',
            'company_id',
            'min_amount',
            'max_amount',
            'allow_cancel_approved_document',
            'active',
            'notify_on_submit',
            'notify_on_next_level',
            'notify_on_final_approval',
            'notify_on_rejection',
            'notify_on_cancellation',
            'setup_state',
            'last_applied_by_id',
            'last_applied_on',
            'apply_error_message',
        }
        before_values = {
            rec.id: {
                field: rec[field]
                for field in tracked_fields
                if field in vals
            }
            for rec in self
        }
        result = super().write(vals)
        skip_change_tracking = (
            self.env.context.get('approval_setup_apply_in_progress')
            or self.env.context.get('approval_internal_setup_apply')
            or self.env.context.get('approval_skip_change_tracking')
            or self.env.context.get('approval_internal_sync')
        )
        if not skip_change_tracking:
            trigger_fields = self._PENDING_TRIGGER_FIELDS.intersection(vals.keys())
            if trigger_fields:
                self._mark_changes_pending()
        if not (
            skip_change_tracking
            or self.env.context.get('approval_setup_suppress_audit_logs')
            or self.env.context.get('install_mode')
            or self.env.context.get('module_uninstall')
        ):
            for rec in self:
                rows = []
                for field_name, old_value in before_values.get(rec.id, {}).items():
                    old_text = rec._format_audit_field_value(field_name, old_value)
                    new_text = rec._format_audit_field_value(field_name, rec[field_name])
                    if old_text != new_text:
                        rows.append((rec._audit_field_label(field_name), old_text, new_text))
                if rows:
                    rec._post_audit_table('Approval Setup Updated', rows)
        return result

    def _compute_user_right_count(self):
        for rec in self:
            if rec.document_type_id:
                rec.user_right_count = self.env['approval.user.right'].search_count([
                    ('document_type_id', '=', rec.document_type_id.id),
                ])
            else:
                rec.user_right_count = 0

    def _compute_rule_line_count(self):
        for rec in self:
            if rec.approval_rule_id:
                rec.rule_line_count = self.env['approval.rule.line'].search_count([
                    ('rule_id', '=', rec.approval_rule_id.id),
                ])
            else:
                rec.rule_line_count = 0

    # ------------------------------------------------------------------
    # Smart-button actions
    # ------------------------------------------------------------------

    def action_open_document_type(self):
        self.ensure_one()
        if not self.document_type_id:
            raise UserError('Apply the setup first to generate a Document Type.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approval Document Type',
            'res_model': 'approval.document.type',
            'res_id': self.document_type_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_approval_rule(self):
        self.ensure_one()
        if not self.approval_rule_id:
            raise UserError('Apply the setup first to generate an Approval Rule.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approval Rule',
            'res_model': 'approval.rule',
            'res_id': self.approval_rule_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_user_rights(self):
        self.ensure_one()
        if not self.document_type_id:
            raise UserError('Apply the setup first to generate Approver Rights.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approver Rights',
            'res_model': 'approval.user.right',
            'domain': [('document_type_id', '=', self.document_type_id.id)],
            'view_mode': 'list,form',
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Main action: Apply Approval Setup
    # ------------------------------------------------------------------

    def action_apply_setup(self):
        """Create or update all backend approval records from this setup."""
        self.ensure_one()
        try:
            setup = self.with_context(
                approval_internal_setup_apply=True,
                approval_skip_change_tracking=True,
                approval_setup_suppress_audit_logs=True,
            )
            setup._validate_before_apply()

            doc_type = setup._apply_document_type()
            rule = setup._apply_approval_rule(doc_type)
            rule_line_stats = setup._apply_rule_lines(rule)
            user_right_stats = setup._apply_user_rights(doc_type)

            setup.write({
                'document_type_id': doc_type.id,
                'approval_rule_id': rule.id,
                'setup_state': 'applied',
                'last_applied_by_id': self.env.user.id,
                'last_applied_on': fields.Datetime.now(),
                'apply_error_message': False,
            })
            self.message_post(
                body=Markup(
                    '<p><strong>Approval Setup Applied</strong></p>'
                    f'<p>Applied By: {escape(self.env.user.name or "-")}<br/>'
                    f'Applied On: {escape(fields.Datetime.to_string(fields.Datetime.now()) or "-")}<br/>'
                    f'Document Type: {escape(doc_type.name or "-")}<br/>'
                    f'Rule Lines Created/Updated: {escape(str(rule_line_stats["created"] + rule_line_stats["updated"]))}<br/>'
                    f'Approver Rights Created/Updated: {escape(str(user_right_stats["created"] + user_right_stats["updated"]))}</p>'
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        except Exception as exc:
            if self.id:
                self.with_context(
                    approval_internal_setup_apply=True,
                    approval_skip_change_tracking=True,
                    approval_setup_suppress_audit_logs=True,
                ).write({
                    'apply_error_message': str(exc),
                })
                self.message_post(
                    body=Markup(
                        '<p><strong>Approval Setup Apply Failed</strong></p>'
                        f'<p>Error: {escape(str(exc))}</p>'
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            raise

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Approval Setup Applied',
                'message': (
                    f'Document Type, Approval Rule, Rule Lines, and '
                    f'Approver Rights have been created / updated for "{self.name}".'
                ),
                'sticky': False,
                'type': 'success',
            },
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_before_apply(self):
        self.ensure_one()
        if not self.approval_integration_module_id:
            raise UserError('Please select a Module before applying.')
        if not self.approval_integration_module_id.is_available:
            raise UserError(
                'The selected approval module is no longer available because its related Odoo module '
                'or approval connector is not installed.'
            )
        if self.approval_integration_module_id.code:
            self.approval_module_code = self.approval_integration_module_id.code
        if not self.approval_module_code:
            raise UserError('Please select a Module before applying.')
        if not self.document_type_id:
            raise UserError('Please select a Document Type before applying.')
        if self.document_type_id.integration_module_id != self.approval_integration_module_id:
            raise UserError(
                'The selected Document Type does not belong to the selected Module. '
                'Please select a valid Document Type.'
            )
        if self.document_type_id.approval_module_code != self.approval_module_code:
            raise UserError(
                'The selected Document Type does not belong to the selected Module. '
                'Please select a valid Document Type.'
            )
        self.document_nature = self.document_type_id.document_nature
        if not self.module_id:
            self.module_id = self._get_preferred_installed_module_for_code(self.approval_module_code)
        if not self.company_id:
            raise UserError('Please select a Company before applying.')
        active_lines = self.setup_line_ids.filtered(lambda l: l.active)
        if not active_lines:
            raise UserError('At least one active Approval Level line is required.')
        for line in active_lines:
            if not line.approval_level_id:
                raise UserError(
                    f'Line {line.sequence}: Approval Level is required.'
                )
            if not line.approver_user_ids:
                raise UserError(
                    f'Line {line.sequence} ({line.approval_level_id.name}): '
                    'At least one Approver is required.'
                )
            if line.minimum_user_count > len(line.approver_user_ids):
                raise UserError(
                    f'Line {line.sequence} ({line.approval_level_id.name}): '
                    f'Minimum Approvers ({line.minimum_user_count}) cannot exceed '
                    f'the number of selected approvers ({len(line.approver_user_ids)}).'
                )
            if line.min_amount < 0:
                raise UserError(
                    f'Line {line.sequence} ({line.approval_level_id.name}): Min Amount must be 0 or greater.'
                )
            if line.max_amount > 0 and line.max_amount < line.min_amount:
                raise UserError(
                    f'Line {line.sequence} ({line.approval_level_id.name}): '
                    'Max Amount must be 0 or greater than/equal to Min Amount.'
                )
            if line.min_amount < 0:
                raise UserError(
                    f'Line {line.sequence} ({line.approval_level_id.name}): Min Amount must be 0 or greater.'
                )
            if line.max_amount > 0 and line.max_amount < line.min_amount:
                raise UserError(
                    f'Line {line.sequence} ({line.approval_level_id.name}): '
                    'Max Amount must be 0 or greater than/equal to Min Amount.'
                )

    def _technical_approval_model(self, model_name):
        """Return a sudoed approval setup model with internal apply context."""
        context = dict(self.env.context, approval_internal_setup_apply=True)
        return self.env[model_name].sudo().with_context(**context)

    # ------------------------------------------------------------------
    # Step 1 – approval.document.type
    # ------------------------------------------------------------------

    def _apply_document_type(self):
        """Create or update the approval.document.type for this setup."""
        self.ensure_one()
        DocumentType = self._technical_approval_model('approval.document.type')

        template_doc_type = self.document_type_id
        if not template_doc_type and self.document_nature:
            template_doc_type = DocumentType.search([
                ('document_nature', '=', self.document_nature),
                '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
            ], order='company_id desc, id desc', limit=1)

        if not template_doc_type and self.document_nature != 'custom':
            raise UserError(
                'No registered Document Type exists for the selected module/document. '
                'Please install the relevant connector module first.'
            )

        vals = {
            'name': self.name,
            'document_nature': self.document_nature,
            'company_id': self.company_id.id,
            'allow_cancel_approved_document': self.allow_cancel_approved_document,
            'notify_on_submit': self.notify_on_submit,
            'notify_on_next_level': self.notify_on_next_level,
            'notify_on_final_approval': self.notify_on_final_approval,
            'notify_on_rejection': self.notify_on_rejection,
            'notify_on_cancellation': self.notify_on_cancellation,
            'active': self.active,
            'is_available': bool(self.approval_integration_module_id and self.approval_integration_module_id.is_available),
            'module_code': template_doc_type.module_code if template_doc_type else self.document_nature,
            'approval_module_code': template_doc_type.approval_module_code if template_doc_type else self.approval_module_code,
            'integration_module_id': (
                template_doc_type.integration_module_id.id
                if template_doc_type and template_doc_type.integration_module_id
                else self.approval_integration_module_id.id
            ),
            'connector_module_name': template_doc_type.connector_module_name if template_doc_type else 'custom_approval_management',
            'target_module_name': template_doc_type.target_module_name if template_doc_type else 'custom_approval_management',
            'document_filter_domain': template_doc_type.document_filter_domain if template_doc_type else '',
            'amount_field_name': template_doc_type.amount_field_name if template_doc_type else '',
            'partner_field_name': template_doc_type.partner_field_name if template_doc_type else '',
            'date_field_name': template_doc_type.date_field_name if template_doc_type else '',
            'document_number_field_name': template_doc_type.document_number_field_name if template_doc_type else '',
        }
        if template_doc_type and template_doc_type.model_id:
            vals['model_id'] = template_doc_type.model_id.id

        technical_vals = {
            'document_nature': vals['document_nature'],
            'company_id': vals['company_id'],
            'module_code': vals['module_code'],
            'approval_module_code': vals['approval_module_code'],
            'integration_module_id': vals['integration_module_id'],
            'connector_module_name': vals['connector_module_name'],
            'target_module_name': vals['target_module_name'],
            'is_available': vals['is_available'],
            'document_filter_domain': vals['document_filter_domain'],
            'amount_field_name': vals['amount_field_name'],
            'partner_field_name': vals['partner_field_name'],
            'date_field_name': vals['date_field_name'],
            'document_number_field_name': vals['document_number_field_name'],
        }
        if vals.get('model_id'):
            technical_vals['model_id'] = vals['model_id']

        if self.document_type_id:
            # Update existing – preserve id, skip duplicate validation re-run
            # by writing fields that may have changed.
            self.document_type_id.sudo().with_context(**DocumentType.env.context).write({
                'name': vals['name'],
                'allow_cancel_approved_document': vals['allow_cancel_approved_document'],
                'notify_on_submit': vals['notify_on_submit'],
                'notify_on_next_level': vals['notify_on_next_level'],
                'notify_on_final_approval': vals['notify_on_final_approval'],
                'notify_on_rejection': vals['notify_on_rejection'],
                'notify_on_cancellation': vals['notify_on_cancellation'],
                'active': vals['active'],
                **technical_vals,
            })
            return self.document_type_id

        # Check whether a matching document type already exists (idempotency)
        existing = DocumentType.search([
            ('document_nature', '=', self.document_nature),
            ('company_id', '=', self.company_id.id),
            ('active', 'in', [True, False]),
        ], limit=1)

        if existing:
            existing.sudo().with_context(**DocumentType.env.context).write({
                'name': vals['name'],
                'allow_cancel_approved_document': vals['allow_cancel_approved_document'],
                'notify_on_submit': vals['notify_on_submit'],
                'notify_on_next_level': vals['notify_on_next_level'],
                'notify_on_final_approval': vals['notify_on_final_approval'],
                'notify_on_rejection': vals['notify_on_rejection'],
                'notify_on_cancellation': vals['notify_on_cancellation'],
                'active': self.active,
                **technical_vals,
            })
            return existing

        # Create brand-new document type
        # Use sudo so that duplicate-prevention constraint context is clean
        return DocumentType.create(vals)

    # ------------------------------------------------------------------
    # Step 2 – approval.rule
    # ------------------------------------------------------------------

    def _apply_approval_rule(self, doc_type):
        """Create or update the approval.rule linked to this setup."""
        self.ensure_one()
        Rule = self._technical_approval_model('approval.rule')
        rule_vals = {
            'name': self.name,
            'document_type_id': doc_type.id,
            'company_id': self.company_id.id,
            'min_amount': self.min_amount,
            'max_amount': self.max_amount,
            'active': self.active,
        }

        if self.approval_rule_id:
            self.approval_rule_id.sudo().with_context(**Rule.env.context).write(rule_vals)
            return self.approval_rule_id

        # Search for an existing rule for this document type + company + amount range
        existing = Rule.search([
            ('document_type_id', '=', doc_type.id),
            ('company_id', '=', self.company_id.id),
            ('min_amount', '=', self.min_amount),
            ('max_amount', '=', self.max_amount),
        ], limit=1)

        if existing:
            existing.sudo().with_context(**Rule.env.context).write(rule_vals)
            return existing

        return Rule.create(rule_vals)

    # ------------------------------------------------------------------
    # Step 3 – approval.rule.line
    # ------------------------------------------------------------------

    def _apply_rule_lines(self, rule):
        """Create or update approval.rule.line records for each setup line."""
        self.ensure_one()
        RuleLine = self._technical_approval_model('approval.rule.line')
        kept_rule_line_ids = []
        stats = {'created': 0, 'updated': 0}

        for line in self.setup_line_ids:
            line_vals = {
                'rule_id': rule.id,
                'sequence': line.sequence,
                'level_id': line.approval_level_id.id,
                'min_amount': line.min_amount,
                'max_amount': line.max_amount,
                'minimum_user_count': line.minimum_user_count,
                'mandatory': line.mandatory,
                'allow_same_user': line.allow_same_user,
                'notification_event_ids': [(6, 0, line.notification_event_ids.ids)],
                'value_lower_limit': line.min_amount,
                'value_upper_limit': line.max_amount,
                'active': line.active,
            }

            if line.rule_line_id:
                # Already linked – update in place
                line.rule_line_id.sudo().with_context(**RuleLine.env.context).write(line_vals)
                stats['updated'] += 1
                kept_rule_line_ids.append(line.rule_line_id.id)
            else:
                # Try to find a matching existing line to avoid duplicates
                existing_rule_line = RuleLine.search([
                    ('rule_id', '=', rule.id),
                    ('sequence', '=', line.sequence),
                    ('level_id', '=', line.approval_level_id.id),
                ], limit=1)
                if existing_rule_line:
                    existing_rule_line.sudo().with_context(**RuleLine.env.context).write(line_vals)
                    line.rule_line_id = existing_rule_line.id
                    stats['updated'] += 1
                    kept_rule_line_ids.append(existing_rule_line.id)
                else:
                    new_rl = RuleLine.create(line_vals)
                    line.rule_line_id = new_rl.id
                    stats['created'] += 1
                    kept_rule_line_ids.append(new_rl.id)

        # Keep Apply idempotent: deactivate orphan rule lines removed from setup.
        stale_rule_lines = RuleLine.search([
            ('rule_id', '=', rule.id),
            ('id', 'not in', kept_rule_line_ids or [0]),
            ('active', '=', True),
        ])
        if stale_rule_lines:
            stale_rule_lines.sudo().with_context(**RuleLine.env.context).write({'active': False})
        return stats

    # ------------------------------------------------------------------
    # Step 4 – approval.user.right
    # ------------------------------------------------------------------

    def _apply_user_rights(self, doc_type):
        """Create or update approval.user.right records for every approver on every line."""
        self.ensure_one()
        UserRight = self._technical_approval_model('approval.user.right')
        desired_keys = set()
        stats = {'created': 0, 'updated': 0}

        for line in self.setup_line_ids.filtered(lambda l: l.active):
            for user in line.approver_user_ids:
                right_vals = {
                    'document_type_id': doc_type.id,
                    'level_id': line.approval_level_id.id,
                    'user_id': user.id,
                    'company_id': self.company_id.id,
                    'value_lower_limit': line.min_amount,
                    'value_upper_limit': line.max_amount,
                    'expiry_date': line.expiry_date,
                    'document_read': line.can_read_document,
                    'active': line.active,
                }
                key = (doc_type.id, line.approval_level_id.id, user.id, self.company_id.id)
                desired_keys.add(key)
                existing = UserRight.search([
                    ('document_type_id', '=', doc_type.id),
                    ('level_id', '=', line.approval_level_id.id),
                    ('user_id', '=', user.id),
                    ('company_id', '=', self.company_id.id),
                ], limit=1)
                if existing:
                    existing.sudo().with_context(**UserRight.env.context).write(right_vals)
                    stats['updated'] += 1
                else:
                    UserRight.create(right_vals)
                    stats['created'] += 1

        # Keep Apply idempotent: disable rights no longer present in setup.
        existing_rights = UserRight.search([
            ('document_type_id', '=', doc_type.id),
            ('company_id', '=', self.company_id.id),
        ])
        stale_rights = existing_rights.filtered(
            lambda r: (r.document_type_id.id, r.level_id.id, r.user_id.id, r.company_id.id) not in desired_keys and r.active
        )
        if stale_rights:
            stale_rights.sudo().with_context(**UserRight.env.context).write({'active': False})
        return stats
