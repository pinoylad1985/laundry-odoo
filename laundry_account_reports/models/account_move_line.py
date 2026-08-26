from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    pos_order_id = fields.Many2one(
        comodel_name='pos.order',
        string='POS Order',
        index='btree_not_null',
        ondelete='set null',
        help="POS order this move line was created for.\n\n"
             "Core creates one receivable line per 'pay later' (Customer Account) "
             "pos.payment when a session closes, but files it on the session's "
             "closing entry and names it after the *session* "
             "('POS/02460 - Customer Account'). Nothing on the line identifies the "
             "order, so two orders from the same customer in the same session are "
             "indistinguishable in Aged Receivable. This field stores that link.",
    )
