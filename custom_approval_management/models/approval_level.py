# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ApprovalLevel(models.Model):
    _name = 'approval.level'
    _description = 'Approval Level'
    _order = 'sequence, name'

    name = fields.Char(required=True, string='Level Name')
    code = fields.Char(required=True, string='Code')
    sequence = fields.Integer(default=10, string='Sequence')
    group_id = fields.Many2one('res.groups', string='Group')
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
        string='Company'
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_company_uniq', 'UNIQUE(code, company_id)', 
         'Code must be unique per company!')
    ]
