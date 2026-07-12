# -*- coding: utf-8 -*-
{
    'name': 'Tezvo Approval Management',
    'version': '19.0.1.0.0',
    'summary': 'Configurable multi-level approval workflow for Odoo business documents.',
    'description': """
Tezvo Approval Management
=========================
Provides a configurable multi-level approval workflow for Odoo transactional
documents, including Sales Orders, Purchase Orders, Vendor Bills, Customer
Invoices, Payments, Journal Entries, Stock Transfers, Manufacturing Orders,
Unbuild Orders, and Scrap Orders.

Features
--------
- Multi-level approval rules with configurable sequences
- Approver rights with amount-based thresholds and expiry
- Approval history and full chatter audit trail
- Colorful Outlook-safe email notifications
- Unified Approval Setup configuration form
- Document-level approval controls

Developed to support streamlined ERP approval processes and operational governance.

Support: tezvosupport@gmail.com
    """,
    'author': 'Tezvo Solutions',
    'website': 'https://www.tezvo.lk/',
    'support': 'tezvosupport@gmail.com',
    'category': 'Tools',
    'depends': ['base', 'mail', 'sale', 'purchase', 'account', 'stock', 'mrp'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/approval_sequence.xml',
        'data/approval_integration_module_data.xml',
        'data/approval_document_type_data.xml',
        'data/approval_notification_event_data.xml',
        'data/approval_delegation_cron.xml',
        'data/approval_delegation_data.xml',
        'data/approval_email_templates.xml',
        'views/approval_level_views.xml',
        'views/approval_document_type_views.xml',
        'views/approval_rule_views.xml',
        'views/approval_user_right_views.xml',
        'views/approval_request_views.xml',
        'views/approval_history_views.xml',
        'views/approval_reject_wizard_views.xml',
        'views/approval_setup_config_views.xml',
        'views/approval_delegation_views.xml',
        'views/sale_order_approval_views.xml',
        'views/purchase_order_approval_views.xml',
        'views/account_move_approval_views.xml',
        'views/account_payment_approval_views.xml',
        'views/stock_picking_approval_views.xml',
        'views/stock_scrap_approval_views.xml',
        'views/mrp_production_approval_views.xml',
        'views/mrp_unbuild_approval_views.xml',
        'views/menu_views.xml',
        'views/res_users_views.xml',
        'report/approval_delegation_report.xml',
        'report/approval_delegation_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_approval_management/static/src/js/my_pending_refresh_stack_fix.js',
        ],
    },
    'installable': True,
    'application': True,
    'price': 399.0,
    'currency': 'USD',
    'license': 'OPL-1',
    'post_init_hook': 'post_init_sync_integrations',
}
