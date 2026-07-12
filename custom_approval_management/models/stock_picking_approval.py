# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class StockPickingApproval(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'approval.mixin']

    approval_state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('waiting', 'Waiting Approval'),
            ('partial', 'Partially Approved'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        copy=False,
        default='draft'
    )
    approval_request_id = fields.Many2one('approval.request', string='Approval Request', copy=False)
    current_approval_level_id = fields.Many2one('approval.level', string='Current Approval Level', copy=False)
    approval_required = fields.Boolean(
        string='Approval Required',
        compute='_compute_approval_required',
        store=False,
        copy=False,
    )

    @api.depends('company_id', 'picking_type_code')
    def _compute_approval_required(self):
        for picking in self:
            picking.approval_required = picking._approval_required_for_document()

    def _approval_get_stock_picking_nature(self):
        self.ensure_one()
        if self.picking_type_code == 'incoming':
            return 'stock_receipt', 'Stock Receipt / GRN'
        if self.picking_type_code == 'outgoing':
            return 'delivery_order', 'Delivery Order / GI'
        if self.picking_type_code == 'internal':
            return 'internal_transfer', 'Internal Transfer'
        return False, False

    def _is_supported_picking_document(self):
        self.ensure_one()
        module_code, _label = self._approval_get_stock_picking_nature()
        return bool(module_code)

    def copy(self, default=None):
        default = dict(default or {})
        default.update({
            'approval_state': 'draft',
            'approval_request_id': False,
            'current_approval_level_id': False,
        })
        return super().copy(default)

    def _get_approval_document_type(self):
        self.ensure_one()
        module_code, _label = self._approval_get_stock_picking_nature()
        if not module_code:
            return self.env['approval.document.type']

        candidates = self.env['approval.document.type'].search([
            ('model_name', '=', 'stock.picking'),
            ('module_code', '=', module_code),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
        ], order='company_id desc, id desc')

        for doc_type in candidates:
            if doc_type.matches_record(self):
                return doc_type
        return self.env['approval.document.type']

    def _get_matching_approval_rule(self, document_type):
        self.ensure_one()
        if not document_type:
            return self.env['approval.rule']
        amount = 0.0
        return self.env['approval.rule'].search([
            ('document_type_id', '=', document_type.id),
            ('active', '=', True),
            ('company_id', 'in', [False, self.company_id.id]),
            ('min_amount', '<=', amount),
            '|', ('max_amount', '=', 0), ('max_amount', '>=', amount),
        ], order='company_id desc, min_amount desc, id desc', limit=1)

    def _approval_required_for_document(self):
        self.ensure_one()
        if not self._is_supported_picking_document():
            return False
        doc_type = self._get_approval_document_type()
        if not doc_type:
            return False
        return bool(self._get_matching_approval_rule(doc_type))

    def _approval_is_configured(self):
        self.ensure_one()
        if not self._is_supported_picking_document():
            return False
        return bool(self._approval_required_for_document() or self._get_approval_document_type())

    def _approval_check_required(self):
        return self._approval_required_for_document()

    def _approval_get_document_type(self):
        return self._get_approval_document_type()

    def _approval_get_amount(self):
        return 0.0

    def _approval_get_partner(self):
        return self.partner_id

    def _approval_get_document_ref(self):
        return self.name or self.origin or '/'

    def action_submit_for_approval(self):
        self.ensure_one()
        _module_code, document_label = self._approval_get_stock_picking_nature()
        if not document_label:
            raise UserError('Submit for Approval is available only for Receipts, Deliveries, and Internal Transfers.')
        if self.state not in ('draft', 'confirmed', 'waiting', 'assigned'):
            raise UserError(f'Only open {document_label} documents can be submitted for approval.')
        if not self.id:
            raise UserError(f'Please save the {document_label} before submitting for approval.')
        if not self._approval_required_for_document():
            raise UserError(f'No approval rule is configured for this {document_label}.')
        if self.approval_request_id and self.approval_request_id.state in ('draft', 'waiting', 'partial'):
            raise UserError(f'An active approval request already exists for this {document_label}.')
        return super().action_submit_for_approval()

    def button_validate(self):
        self._check_approval_before_business_action()
        return super().button_validate()

    def _approval_cancel_linked_request(self, reason=''):
        self.ensure_one()
        request = self.approval_request_id
        if not request:
            return
        if request.state not in ['cancelled', 'rejected', 'approved']:
            request.state = 'cancelled'
            pending_lines = request.request_line_ids.filtered(lambda l: l.state == 'pending')
            pending_lines.write({
                'state': 'cancelled',
                'remarks': reason or 'Document was cancelled.',
            })
            request._create_history('cancelled', reason or 'Document was cancelled.')

    def action_cancel(self):
        for picking in self:
            if (
                picking._approval_is_configured()
                and picking.approval_state == 'approved'
                and picking.state == 'done'
            ):
                document_type = picking._approval_get_document_type()
                if document_type and not document_type.allow_cancel_approved_document:
                    _module_code, document_label = picking._approval_get_stock_picking_nature()
                    raise UserError(
                        f'This {document_label} has already been approved and cannot be cancelled '
                        'based on the approval configuration.'
                    )

        result = super().action_cancel()

        for picking in self:
            if picking.approval_request_id:
                _module_code, document_label = picking._approval_get_stock_picking_nature()
                if document_label:
                    picking._approval_cancel_linked_request(f'{document_label} was cancelled.')
                else:
                    picking._approval_cancel_linked_request('Transfer was cancelled.')
                picking.write({
                    'approval_state': 'cancelled',
                    'current_approval_level_id': False,
                })

        return result

    def _approval_create_request(self):
        self.ensure_one()

        _module_code, document_label = self._approval_get_stock_picking_nature()
        if not document_label:
            raise UserError('Approval is supported only for Receipts, Deliveries, and Internal Transfers.')

        if not self.id:
            raise UserError(f'Please save the {document_label} before submitting for approval.')

        doc_type = self._get_approval_document_type()
        if not doc_type:
            raise UserError(f'No approval rule is configured for this {document_label}.')

        applicable_rule = self._get_matching_approval_rule(doc_type)
        if not applicable_rule:
            raise UserError(f'No approval rule is configured for this {document_label}.')

        request_lines = []
        for rule_line in applicable_rule._get_applicable_rule_lines(self._approval_get_amount()):
            request_lines.append((0, 0, {
                'sequence': rule_line.sequence,
                'level_id': rule_line.level_id.id,
                'minimum_user_count': rule_line.minimum_user_count,
                'mandatory': rule_line.mandatory,
                'allow_same_user': rule_line.allow_same_user,
                'notification_event_ids': [(6, 0, rule_line.notification_event_ids.ids)],
            }))

        previous_requests = self.env['approval.request'].search([
            ('res_model', '=', 'stock.picking'),
            ('res_id', '=', self.id),
        ])
        cycle_no = len(previous_requests) + 1

        approval_request = self.env['approval.request'].with_context(
            approval_request_internal_create=True
        ).create({
            'name': f'{document_label} Approval: {self.name or self.origin or "/"}',
            'document_type_id': doc_type.id,
            'res_model': 'stock.picking',
            'res_id': self.id,
            'document_ref': self.name or self.origin or '/',
            'partner_id': self.partner_id.id if self.partner_id else False,
            'amount_total': 0.0,
            'submitted_by': self.env.user.id,
            'submitted_date': fields.Datetime.now(),
            'company_id': self.company_id.id,
            'state': 'waiting',
            'cycle_no': cycle_no,
            'request_line_ids': request_lines,
        })

        first_line = approval_request.request_line_ids.sorted('sequence')[:1]
        if first_line:
            approval_request.current_level_id = first_line.level_id.id

        self.approval_request_id = approval_request.id
        self.approval_state = 'waiting'
        self.current_approval_level_id = approval_request.current_level_id.id if approval_request.current_level_id else False

        return approval_request

    def _approval_on_final_approved(self):
        self.approval_state = 'approved'
        self.current_approval_level_id = False

    def _approval_on_rejected(self):
        self.approval_state = 'rejected'
        self.current_approval_level_id = False

    def _approval_on_cancelled(self):
        self.approval_state = 'cancelled'
        self.current_approval_level_id = False
