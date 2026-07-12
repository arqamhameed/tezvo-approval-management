# -*- coding: utf-8 -*-
from odoo import api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval


def _is_real_database_id(record_id):
    """Return True only for persisted database integer IDs.

    Compatible with Odoo versions where onchange IDs may be temporary
    NewId-like objects or strings.
    """
    return isinstance(record_id, int) and record_id > 0 and not str(record_id).startswith('NewId')

# Predefined configuration for each known business document nature.
_NATURE_CONFIG = {
    'purchase_order': {
        'model': 'purchase.order',
        'module_code': 'purchase',
        'document_filter_domain': '',
        'amount_field_name': 'amount_total',
        'partner_field_name': 'partner_id',
        'date_field_name': 'date_order',
        'document_number_field_name': 'name',
    },
    'vendor_bill': {
        'model': 'account.move',
        'module_code': 'vendor_bill',
        'document_filter_domain': "[('move_type', '=', 'in_invoice')]",
        'amount_field_name': 'amount_total',
        'partner_field_name': 'partner_id',
        'date_field_name': 'invoice_date',
        'document_number_field_name': 'name',
    },
    'vendor_credit_note': {
        'model': 'account.move',
        'module_code': 'vendor_credit_note',
        'document_filter_domain': "[('move_type', '=', 'in_refund')]",
        'amount_field_name': 'amount_total',
        'partner_field_name': 'partner_id',
        'date_field_name': 'invoice_date',
        'document_number_field_name': 'name',
    },
    'customer_invoice': {
        'model': 'account.move',
        'module_code': 'customer_invoice',
        'document_filter_domain': "[('move_type', '=', 'out_invoice')]",
        'amount_field_name': 'amount_total',
        'partner_field_name': 'partner_id',
        'date_field_name': 'invoice_date',
        'document_number_field_name': 'name',
    },
    'customer_credit_note': {
        'model': 'account.move',
        'module_code': 'customer_credit_note',
        'document_filter_domain': "[('move_type', '=', 'out_refund')]",
        'amount_field_name': 'amount_total',
        'partner_field_name': 'partner_id',
        'date_field_name': 'invoice_date',
        'document_number_field_name': 'name',
    },
    'account_journal_entry': {
        'model': 'account.move',
        'module_code': 'account_journal_entry',
        'document_filter_domain': "[('move_type', '=', 'entry')]",
        'amount_field_name': '',
        'partner_field_name': 'partner_id',
        'date_field_name': 'date',
        'document_number_field_name': 'name',
    },
    'vendor_payment': {
        'model': 'account.payment',
        'module_code': 'vendor_payment',
        'document_filter_domain': "[('payment_type', '=', 'outbound'), ('partner_type', '=', 'supplier')]",
        'amount_field_name': 'amount',
        'partner_field_name': 'partner_id',
        'date_field_name': 'date',
        'document_number_field_name': 'name',
    },
    'customer_payment': {
        'model': 'account.payment',
        'module_code': 'customer_payment',
        'document_filter_domain': "[('payment_type', '=', 'inbound'), ('partner_type', '=', 'customer')]",
        'amount_field_name': 'amount',
        'partner_field_name': 'partner_id',
        'date_field_name': 'date',
        'document_number_field_name': 'name',
    },
    'stock_receipt': {
        'model': 'stock.picking',
        'module_code': 'stock_receipt',
        'document_filter_domain': "[('picking_type_code', '=', 'incoming')]",
        'amount_field_name': '',
        'partner_field_name': 'partner_id',
        'date_field_name': 'scheduled_date',
        'document_number_field_name': 'name',
    },
    'delivery_order': {
        'model': 'stock.picking',
        'module_code': 'delivery_order',
        'document_filter_domain': "[('picking_type_code', '=', 'outgoing')]",
        'amount_field_name': '',
        'partner_field_name': 'partner_id',
        'date_field_name': 'scheduled_date',
        'document_number_field_name': 'name',
    },
    'internal_transfer': {
        'model': 'stock.picking',
        'module_code': 'internal_transfer',
        'document_filter_domain': "[('picking_type_code', '=', 'internal')]",
        'amount_field_name': '',
        'partner_field_name': 'partner_id',
        'date_field_name': 'scheduled_date',
        'document_number_field_name': 'name',
    },
    'sales_order': {
        'model': 'sale.order',
        'module_code': 'sales_order',
        'document_filter_domain': '',
        'amount_field_name': 'amount_total',
        'partner_field_name': 'partner_id',
        'date_field_name': 'date_order',
        'document_number_field_name': 'name',
    },
    'mrp_production': {
        'model': 'mrp.production',
        'module_code': 'mrp_production',
        'document_filter_domain': '',
        'amount_field_name': '',
        'partner_field_name': '',
        'date_field_name': 'date_start',
        'document_number_field_name': 'name',
    },
    'mrp_unbuild': {
        'model': 'mrp.unbuild',
        'module_code': 'mrp_unbuild',
        'document_filter_domain': '',
        'amount_field_name': '',
        'partner_field_name': '',
        'date_field_name': 'create_date',
        'document_number_field_name': 'name',
    },
    'stock_scrap': {
        'model': 'stock.scrap',
        'module_code': 'stock_scrap',
        'document_filter_domain': '',
        'amount_field_name': '',
        'partner_field_name': '',
        'date_field_name': 'create_date',
        'document_number_field_name': 'name',
    },
    'approval_delegation': {
        'model': 'approval.delegation',
        'module_code': 'approval_delegation',
        'document_filter_domain': '',
        'amount_field_name': '',
        'partner_field_name': '',
        'date_field_name': 'date_from',
        'document_number_field_name': 'name',
    },
}

_EXCLUDED_STANDARD_NATURES = ('mrp_bom', 'account_asset', 'account_transfer_model')

# Reverse mapping: module_code -> document_nature (used for backward-compat migration)
_MODULE_CODE_TO_NATURE = {cfg['module_code']: nature for nature, cfg in _NATURE_CONFIG.items()}
_MODULE_CODE_TO_NATURE['payment'] = 'vendor_payment'  # legacy code from earlier versions

_NATURE_TO_APPROVAL_MODULE = {
    'purchase_order': 'purchase',
    'sale_order': 'sale',
    'sales_order': 'sale',
    'vendor_bill': 'account',
    'vendor_credit_note': 'account',
    'customer_invoice': 'account',
    'customer_credit_note': 'account',
    'vendor_payment': 'account',
    'customer_payment': 'account',
    'account_journal_entry': 'account',
    'stock_receipt': 'stock',
    'delivery_order': 'stock',
    'internal_transfer': 'stock',
    'stock_scrap': 'stock',
    'mrp_production': 'mrp',
    'mrp_unbuild': 'mrp',
    'approval_delegation': 'custom_approval_management',
}

_APPROVAL_MODULE_SELECTION = [
    ('purchase', 'Purchase'),
    ('sale', 'Sales'),
    ('account', 'Accounting'),
    ('stock', 'Inventory'),
    ('mrp', 'Manufacturing'),
    ('custom_approval_management', 'Approval Management'),
]

_LEGACY_NATURE_CONNECTOR_MAP = {
    'purchase_order': ('custom_approval_purchase', 'purchase'),
    'sales_order': ('custom_approval_sale', 'sale'),
    'vendor_bill': ('custom_approval_account', 'account'),
    'vendor_credit_note': ('custom_approval_account', 'account'),
    'customer_invoice': ('custom_approval_account', 'account'),
    'customer_credit_note': ('custom_approval_account', 'account'),
    'vendor_payment': ('custom_approval_account', 'account'),
    'customer_payment': ('custom_approval_account', 'account'),
    'account_journal_entry': ('custom_approval_account', 'account'),
    'stock_receipt': ('custom_approval_stock', 'stock'),
    'delivery_order': ('custom_approval_stock', 'stock'),
    'internal_transfer': ('custom_approval_stock', 'stock'),
    'stock_scrap': ('custom_approval_stock', 'stock'),
    'mrp_production': ('custom_approval_mrp', 'mrp'),
    'mrp_unbuild': ('custom_approval_mrp', 'mrp'),
    'approval_delegation': ('custom_approval_management', 'custom_approval_management'),
}

_APPROVAL_MODULE_INSTALL_CHECK = {
    'purchase': ['purchase'],
    'sale': ['sale_management', 'sale'],
    'account': ['account'],
    'stock': ['stock'],
    'mrp': ['mrp'],
    'custom_approval_management': ['custom_approval_management'],
}


class ApprovalDocumentType(models.Model):
    _name = 'approval.document.type'
    _description = 'Approval Document Type'
    _order = 'name'

    name = fields.Char(required=True, string='Document Type Name')
    document_nature = fields.Selection(
        selection=[
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
        ],
        string='Document Type',
        default='custom',
        help=(
            'Select the transactional business document type. '
            'The system will automatically set the technical module code, '
            'document filter domain, and field mappings. '
            'Choose Custom / Advanced to configure all fields manually. '
            'Each document type can be configured only once per company.'
        ),
    )
    model_id = fields.Many2one(
        'ir.model',
        required=True,
        string='Model',
        ondelete='cascade',
        domain=[('transient', '=', False)]
    )
    model_name = fields.Char(
        related='model_id.model',
        store=True,
        string='Model Name'
    )
    res_model = fields.Char(
        related='model_name',
        store=True,
        string='Resource Model',
    )
    integration_module_id = fields.Many2one(
        'approval.integration.module',
        string='Integration Module',
        ondelete='set null',
    )
    module_code = fields.Char(
        string='Module Code',
        help='Technical key used by the approval engine. Filled automatically based on Document Type.'
    )
    approval_module_code = fields.Selection(
        selection=_APPROVAL_MODULE_SELECTION,
        string='Module',
        index=True,
        help='Business module grouping used by Approval Setup filtering.',
    )
    connector_module_name = fields.Char(
        string='Connector Module',
        help='Technical connector module that registers this document type.',
    )
    target_module_name = fields.Char(
        string='Target Module',
        help='Business module required for this document type.',
    )
    is_available = fields.Boolean(
        default=True,
        index=True,
        help='True when the owning integration module is currently available.',
    )
    amount_field_name = fields.Char(
        string='Amount Field Name',
        help='Field name to get the document amount. Filled automatically based on Document Type.'
    )
    partner_field_name = fields.Char(
        string='Partner Field Name',
        help='Field name to get the document partner. Filled automatically based on Document Type.'
    )
    date_field_name = fields.Char(
        string='Date Field Name',
        help='Field name to get the document date. Filled automatically based on Document Type.'
    )
    document_number_field_name = fields.Char(
        string='Document Number Field Name',
        help='Field name to get the document number. Filled automatically based on Document Type.'
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        string='Company'
    )
    allow_cancel_approved_document = fields.Boolean(
        default=False,
        string='Allow Cancellation After Approval',
        help='If enabled, users can cancel documents after approval. If disabled, approved documents cannot be cancelled.'
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
    document_filter_domain = fields.Text(
        string='Document Filter Domain',
        help=(
            'Technical filter used to separate documents that share the same Odoo model. '
            'Filled automatically based on Document Type. '
            'Leave empty to match all documents of this model. '
            "Example for Vendor Bills: [('move_type', '=', 'in_invoice')] "
            "Example for GRN: [('picking_type_code', '=', 'incoming')]"
        )
    )
    active = fields.Boolean(default=True)

    # -------------------------------------------------------------------------
    # Auto-fill logic
    # -------------------------------------------------------------------------

    @api.onchange('document_nature')
    def _onchange_document_nature(self):
        """Auto-fill technical fields when a known document nature is selected."""
        if not self.document_nature or self.document_nature == 'custom':
            return
        config = _NATURE_CONFIG.get(self.document_nature, {})
        if not config:
            return
        model_name = config.get('model', '')
        if model_name:
            ir_model = self.env['ir.model'].search([('model', '=', model_name)], limit=1)
            self.model_id = ir_model.id if ir_model else False
        self.module_code = config.get('module_code', '')
        self.approval_module_code = _NATURE_TO_APPROVAL_MODULE.get(self.document_nature)
        self.document_filter_domain = config.get('document_filter_domain', '')
        self.amount_field_name = config.get('amount_field_name', '')
        self.partner_field_name = config.get('partner_field_name', '')
        self.date_field_name = config.get('date_field_name', '')
        self.document_number_field_name = config.get('document_number_field_name', '')

    @api.model
    def _apply_nature_config(self, vals, nature):
        """Apply predefined configuration for a given nature to a vals dict (in-place)."""
        config = _NATURE_CONFIG.get(nature, {})
        if not config:
            return
        model_name = config.get('model', '')
        if model_name:
            ir_model = self.env['ir.model'].search([('model', '=', model_name)], limit=1)
            vals['model_id'] = ir_model.id if ir_model else vals.get('model_id', False)
        vals['module_code'] = config.get('module_code', '')
        vals['approval_module_code'] = _NATURE_TO_APPROVAL_MODULE.get(nature)
        vals['document_filter_domain'] = config.get('document_filter_domain', '')
        vals['amount_field_name'] = config.get('amount_field_name', '')
        vals['partner_field_name'] = config.get('partner_field_name', '')
        vals['date_field_name'] = config.get('date_field_name', '')
        vals['document_number_field_name'] = config.get('document_number_field_name', '')

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

    @api.model_create_multi
    def create(self, vals_list):
        # Backend protection: block manual creation unless internal flag is present
        if not self._is_internal_approval_operation():
            raise UserError('Manual creation is not allowed for Back Rules and Setup records. Please use Approval Setup.')
        
        for vals in vals_list:
            nature = vals.get('document_nature', 'custom')
            if nature in _EXCLUDED_STANDARD_NATURES:
                vals['active'] = False
                vals['document_nature'] = 'custom'
            if nature and nature != 'custom':
                self._apply_nature_config(vals, nature)
            elif not vals.get('document_nature') and vals.get('module_code'):
                # Backward compat: infer document_nature from module_code on create
                inferred = _MODULE_CODE_TO_NATURE.get(vals['module_code'])
                if inferred:
                    vals['document_nature'] = inferred
            if vals.get('document_nature') and not vals.get('approval_module_code'):
                vals['approval_module_code'] = _NATURE_TO_APPROVAL_MODULE.get(vals['document_nature'])
        records = super().create(vals_list)
        records._validate_duplicate_nature_per_company()
        return records

    def write(self, vals):
        # Backend protection: block manual write unless user is in special group or internal flag is present
        if not self._is_internal_approval_operation():
            if not self.env.user.has_group('custom_approval_management.group_approval_back_rules_editor'):
                raise UserError('You do not have permission to edit Back Rules and Setup records. Please contact an Approval Back Rules Editor.')
        
        nature = vals.get('document_nature')
        if nature in _EXCLUDED_STANDARD_NATURES:
            vals['active'] = False
            vals['document_nature'] = 'custom'
            nature = 'custom'
        if nature and nature != 'custom':
            self._apply_nature_config(vals, nature)
        if nature:
            vals['approval_module_code'] = _NATURE_TO_APPROVAL_MODULE.get(nature)
        result = super().write(vals)
        self._validate_duplicate_nature_per_company()
        return result
    
    def unlink(self):
        # Backend protection: block manual deletion
        if not self._is_internal_approval_operation():
            raise UserError('Deletion is not allowed for Back Rules and Setup records. Please archive or deactivate where applicable.')
        return super().unlink()
    
    def copy(self, default=None):
        # Backend protection: block manual duplication
        from odoo.exceptions import UserError
        raise UserError('Duplicate is not allowed for Back Rules and Setup records.')

    @api.constrains('document_nature', 'company_id', 'active')
    def _check_duplicate_nature_per_company(self):
        self._validate_duplicate_nature_per_company()

    def _validate_duplicate_nature_per_company(self):
        """Block duplicate non-custom nature per company and global/company conflicts."""
        for record in self:
            if not record.document_nature or record.document_nature == 'custom':
                continue

            if record.company_id:
                # Company-specific cannot duplicate itself and cannot coexist with global.
                domain = [
                    ('id', '!=', record.id),
                    ('document_nature', '=', record.document_nature),
                    '|',
                    ('company_id', '=', False),
                    ('company_id', '=', record.company_id.id),
                ]
            else:
                # Global is exclusive: it cannot coexist with any company-specific/global record.
                domain = [
                    ('id', '!=', record.id),
                    ('document_nature', '=', record.document_nature),
                ]

            if self.search_count(domain):
                raise ValidationError(
                    'An Approval Document Type for this Document Type and Company already exists.'
                )

    # -------------------------------------------------------------------------
    # Upgrade migration: backfill document_nature from module_code
    # -------------------------------------------------------------------------

    def _auto_init(self):
        result = super()._auto_init()
        try:
            for module_code, nature in _MODULE_CODE_TO_NATURE.items():
                self.env.cr.execute(
                    """
                    UPDATE approval_document_type
                       SET document_nature = %s
                     WHERE module_code = %s
                       AND (document_nature IS NULL OR document_nature = 'custom')
                    """,
                    (nature, module_code),
                )

            # Enforce transactional-only standard options: archive master/configuration natures.
            self.env.cr.execute(
                """
                UPDATE approval_document_type
                   SET active = FALSE
                 WHERE document_nature IN %s
                """,
                (_EXCLUDED_STANDARD_NATURES,),
            )

            for nature, module_code in _NATURE_TO_APPROVAL_MODULE.items():
                self.env.cr.execute(
                    """
                    UPDATE approval_document_type
                       SET approval_module_code = %s
                     WHERE document_nature = %s
                       AND COALESCE(approval_module_code, '') != %s
                    """,
                    (module_code, nature, module_code),
                )

            for nature, mapping in _LEGACY_NATURE_CONNECTOR_MAP.items():
                connector_module_name, target_module_name = mapping
                self.env.cr.execute(
                    """
                    UPDATE approval_document_type
                       SET connector_module_name = %s,
                           target_module_name = %s
                     WHERE document_nature = %s
                       AND (
                            COALESCE(connector_module_name, '') != %s
                            OR COALESCE(target_module_name, '') != %s
                       )
                    """,
                    (connector_module_name, target_module_name, nature, connector_module_name, target_module_name),
                )

            integration_model = self.env['approval.integration.module'].sudo()
            defaults = {
                'approval_management': {
                    'name': 'Approval Management',
                    'connector_module_name': 'custom_approval_management',
                    'target_module_names': 'custom_approval_management',
                    'sequence': 10,
                    'description': 'Core approval engine integration.',
                },
                'sale': {
                    'name': 'Sales',
                    'connector_module_name': 'custom_approval_sale',
                    'target_module_names': 'sale_management,sale',
                    'sequence': 20,
                    'description': 'Sales approvals connector integration.',
                },
                'purchase': {
                    'name': 'Purchase',
                    'connector_module_name': 'custom_approval_purchase',
                    'target_module_names': 'purchase',
                    'sequence': 30,
                    'description': 'Purchase approvals connector integration.',
                },
                'account': {
                    'name': 'Accounting',
                    'connector_module_name': 'custom_approval_account',
                    'target_module_names': 'account',
                    'sequence': 40,
                    'description': 'Accounting approvals connector integration.',
                },
                'stock': {
                    'name': 'Inventory',
                    'connector_module_name': 'custom_approval_stock',
                    'target_module_names': 'stock',
                    'sequence': 50,
                    'description': 'Inventory approvals connector integration.',
                },
                'mrp': {
                    'name': 'Manufacturing',
                    'connector_module_name': 'custom_approval_mrp',
                    'target_module_names': 'mrp',
                    'sequence': 60,
                    'description': 'Manufacturing approvals connector integration.',
                },
            }

            integration_by_code = {}
            for code, vals in defaults.items():
                integration = integration_model.ensure_integration({'code': code, **vals})
                integration_by_code[code] = integration

            for doc_type in self.sudo().search([]):
                code = doc_type.approval_module_code or (
                    'approval_management' if doc_type.document_nature in ('approval_delegation', 'custom') else False
                )
                integration = integration_by_code.get(code) if code else False
                if integration and doc_type.integration_module_id != integration:
                    doc_type.integration_module_id = integration.id

            integration_model.sync_integrations()
        except Exception:
            pass  # Table may not exist yet on fresh install
        return result

    # -------------------------------------------------------------------------
    # Domain matching
    # -------------------------------------------------------------------------

    def matches_record(self, record):
        """Return True if this document type applies to the given record.

        If document_filter_domain is empty, always matches.
        Evaluates the domain safely and checks if the record satisfies it.

        Safe for both saved records (real integer id) and unsaved onchange
        records that carry a temporary NewId.  In the unsaved case the domain
        is evaluated in-memory via record.filtered_domain() so no SQL is
        executed and no NewId is sent to the database.
        """
        self.ensure_one()

        if not record:
            return False

        # Guard: model must match.
        model_name = self._get_model_name()
        if record._name != model_name:
            return False

        domain_text = self.document_filter_domain or ''
        if not domain_text.strip():
            return True

        try:
            domain = safe_eval(domain_text)
        except Exception as e:
            raise UserError(
                f'Invalid Document Filter Domain on approval document type "{self.name}".\n'
                f'Domain: {self.document_filter_domain}\n'
                f'Error: {e}'
            )

        if not isinstance(domain, list):
            raise UserError(
                f'Document Filter Domain on "{self.name}" must be a valid Odoo domain list.\n'
                f'Got: {self.document_filter_domain}'
            )

        if not domain:
            return True

        # Determine whether the record has a real persisted id.
        # record._origin.id holds the original DB id when inside an onchange;
        # it is falsy for brand-new unsaved records.
        real_id = False
        origin = getattr(record, '_origin', None)
        record_id = record.id
        origin_id = origin.id if origin is not None and origin.id else False

        if _is_real_database_id(record_id):
            real_id = record_id
        elif _is_real_database_id(origin_id):
            real_id = origin_id

        if real_id:
            # Saved record: use DB search for accuracy.
            try:
                full_domain = [('id', '=', real_id)] + domain
                return bool(self.env[model_name].search(full_domain, limit=1))
            except Exception:
                pass  # Fall through to in-memory check on unexpected failure.

        # Unsaved / onchange record: evaluate domain in-memory to avoid
        # passing a NewId string to the database.
        try:
            return bool(record.filtered_domain(domain))
        except Exception:
            # In-memory evaluation failed for some reason; treat as non-match
            # rather than crashing.  Approval will be re-evaluated after save.
            return False

    def _get_model_name(self):
        """Return the technical model name for this document type."""
        self.ensure_one()

        # Prefer stored char value first so business users do not need read
        # access to ir.model when approval checks run on transactional forms.
        if self.model_name:
            return self.model_name

        # Fallback to technical metadata with sudo.
        if self.model_id:
            return self.model_id.sudo().model

        return False

