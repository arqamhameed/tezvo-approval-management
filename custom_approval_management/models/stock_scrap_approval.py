# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class StockScrapApproval(models.Model):
    _name = 'stock.scrap'
    _inherit = ['stock.scrap', 'approval.mixin']

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

    @api.depends('company_id')
    def _compute_approval_required(self):
        for scrap in self:
            scrap.approval_required = scrap._approval_required_for_document()

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
        candidates = self.env['approval.document.type'].search([
            ('model_name', '=', 'stock.scrap'),
            ('module_code', '=', 'stock_scrap'),
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
        doc_type = self._get_approval_document_type()
        if not doc_type:
            return False
        return bool(self._get_matching_approval_rule(doc_type))

    def _approval_check_required(self):
        return self._approval_required_for_document()

    def _approval_get_document_type(self):
        return self._get_approval_document_type()

    def _approval_get_amount(self):
        return 0.0

    def _approval_get_partner(self):
        return False

    def _approval_get_document_ref(self):
        return self.name or '/'

    def action_submit_for_approval(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('Only draft Scrap Orders can be submitted for approval.')
        if not self.id:
            raise UserError('Please save the Scrap Order before submitting for approval.')
        if not self._approval_required_for_document():
            raise UserError('No approval rule is configured for this Scrap Order.')
        if self.approval_request_id and self.approval_request_id.state in ('draft', 'waiting', 'partial'):
            raise UserError('An active approval request already exists for this Scrap Order.')
        return super().action_submit_for_approval()

    def action_validate(self):
        self._check_approval_before_business_action()
        return super().action_validate()

    def _approval_create_request(self):
        self.ensure_one()

        if not self.id:
            raise UserError('Please save the Scrap Order before submitting for approval.')

        doc_type = self._get_approval_document_type()
        if not doc_type:
            raise UserError('No approval rule is configured for this Scrap Order.')

        applicable_rule = self._get_matching_approval_rule(doc_type)
        if not applicable_rule:
            raise UserError('No approval rule is configured for this Scrap Order.')

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
            ('res_model', '=', 'stock.scrap'),
            ('res_id', '=', self.id),
        ])
        cycle_no = len(previous_requests) + 1

        approval_request = self.env['approval.request'].with_context(
            approval_request_internal_create=True
        ).create({
            'name': f'Scrap Order Approval: {self.name or "/"}',
            'document_type_id': doc_type.id,
            'res_model': 'stock.scrap',
            'res_id': self.id,
            'document_ref': self.name or '/',
            'partner_id': False,
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
