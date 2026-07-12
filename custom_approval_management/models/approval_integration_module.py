# -*- coding: utf-8 -*-
from odoo import api, fields, models


# Standard document type mappings: document_nature → integration_code
_STANDARD_DOCUMENT_TYPES = {
    'purchase_order': 'purchase',
    'vendor_bill': 'account',
    'vendor_credit_note': 'account',
    'customer_invoice': 'account',
    'customer_credit_note': 'account',
    'account_journal_entry': 'account',
    'vendor_payment': 'account',
    'customer_payment': 'account',
    'sales_order': 'sale',
    'stock_receipt': 'stock',
    'delivery_order': 'stock',
    'internal_transfer': 'stock',
    'stock_scrap': 'stock',
    'mrp_production': 'mrp',
    'mrp_unbuild': 'mrp',
    'approval_delegation': 'custom_approval_management',
}


class ApprovalIntegrationModule(models.Model):
    _name = 'approval.integration.module'
    _description = 'Approval Integration Module Registry'
    _order = 'sequence, name'

    name = fields.Char(required=True, string='Name')
    code = fields.Char(required=True, string='Code', index=True, unique=True)
    connector_module_name = fields.Char(string='Connector Module Name')
    target_module_names = fields.Char(
        string='Target Module Names',
        help='Comma-separated technical module names to check for availability.'
    )
    active = fields.Boolean(default=True)
    is_available = fields.Boolean(default=False, index=True)
    sequence = fields.Integer(default=10)
    description = fields.Text()

    @api.model
    def _module_is_installed(self, module_name):
        """Check if a single module is installed."""
        if not module_name:
            return False
        return bool(self.env['ir.module.module'].sudo().search_count([
            ('name', '=', module_name),
            ('state', '=', 'installed'),
        ]))

    def _targets_installed(self):
        """Check if any of the target modules are installed."""
        self.ensure_one()
        targets = [name.strip() for name in (self.target_module_names or '').split(',') if name.strip()]
        if not targets:
            return False
        return any(self._module_is_installed(name) for name in targets)

    @api.model
    def sync_integrations(self):
        """Sync integration modules and document types.
        
        This method:
        1. Updates is_available for each integration based on installed Odoo modules
        2. For each standard document type, ensures it exists and is linked to its integration
        3. Updates document type is_available and active status based on integration availability
        
        Safe to call multiple times (idempotent).
        Does not delete records.
        """
        integrations = self.sudo().search([])
        module_model = self.env['ir.module.module'].sudo()
        doc_model = self.env['approval.document.type'].sudo().with_context(approval_internal_sync=True)
        
        # Step 1: Update integration availability based on installed modules
        for integration in integrations:
            # Special case: Approval Management is available if core module is installed
            if integration.code == 'approval_management':
                available = bool(module_model.search_count([
                    ('name', '=', 'custom_approval_management'),
                    ('state', '=', 'installed')
                ]))
            else:
                # Check if any target module is installed
                available = integration._targets_installed()
            
            # Update if changed
            if integration.is_available != bool(available):
                integration.sudo().write({'is_available': bool(available)})
        
        # Step 2: Process all standard document types
        for doc_nature, integration_code in _STANDARD_DOCUMENT_TYPES.items():
            # Find the integration module
            integration = self.sudo().search([('code', '=', integration_code)], limit=1)
            if not integration:
                continue
            
            # Search for the document type by nature
            doc = doc_model.search([('document_nature', '=', doc_nature)], limit=1)
            
            if doc:
                # Document exists: update its integration link and availability
                vals = {
                    'integration_module_id': integration.id,
                    'is_available': integration.is_available,
                    'active': True,  # Keep standard document types active
                }
                try:
                    doc_model.browse(doc.id).write(vals)
                except Exception:
                    pass
            # Note: Document type creation is handled by data XML; we only link/sync here
        
        # Step 3: Backfill any remaining document types with integration links
        # (for custom document types or those not in standard mapping)
        for doc in doc_model.search([]):
            if not doc.integration_module_id or not doc.approval_module_code:
                integration = False
                
                # Try to match by approval_module_code
                if doc.approval_module_code:
                    integration = self.sudo().search([
                        ('code', '=', doc.approval_module_code)
                    ], limit=1)
                
                # Fallback: use standard mapping if available
                if not integration and doc.document_nature:
                    integration_code = _STANDARD_DOCUMENT_TYPES.get(doc.document_nature)
                    if integration_code:
                        integration = self.sudo().search([('code', '=', integration_code)], limit=1)
                
                # Fallback: match by target module name
                if not integration and doc.target_module_name:
                    integration = self.sudo().search([
                        ('target_module_names', 'like', doc.target_module_name)
                    ], limit=1)
                
                # Link and sync availability
                if integration:
                    vals = {
                        'integration_module_id': integration.id,
                        'is_available': integration.is_available,
                    }
                    try:
                        doc_model.browse(doc.id).write(vals)
                    except Exception:
                        pass
        
        return True

    def action_sync_integrations(self):
        """Server action wrapper for syncing integrations."""
        self.sync_integrations()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
