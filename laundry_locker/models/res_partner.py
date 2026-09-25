from odoo import api, fields, models

from .laundry_locker_transaction import phone_last10


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Stored + indexed so matching a locker transaction to a customer is an
    # equality lookup. Computing it on the fly would mean scanning and
    # normalising every partner's phone on every match.
    laundry_phone_last10 = fields.Char(
        string='Phone Key (last 10)', compute='_compute_laundry_phone_last10',
        store=True, index=True,
        help="Last 10 digits of the phone number - the key a locker "
             "transaction is matched to a customer on.",
    )

    @api.depends('phone')
    def _compute_laundry_phone_last10(self):
        for partner in self:
            partner.laundry_phone_last10 = phone_last10(partner.phone)

    # A transaction is matched when it arrives, but the customer often does not
    # exist yet at that point - they are created at the counter, from that very
    # transaction or from a walk-in minutes earlier. Without this, the row would
    # stay "New" and the cashier would create a second contact for the same
    # person. Only UNBILLED, still-unmatched rows are touched, so a match a
    # cashier corrected by hand is never overwritten.
    def _laundry_rematch_locker_transactions(self):
        keys = [p.laundry_phone_last10 for p in self if p.laundry_phone_last10]
        if not keys:
            return
        pending = self.env['laundry.locker.transaction'].sudo().search([
            ('billed', '=', False),
            ('partner_id', '=', False),
            ('phone_last10', 'in', keys),
        ])
        if pending:
            pending._laundry_rematch_partner()

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners._laundry_rematch_locker_transactions()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if 'phone' in vals:
            self._laundry_rematch_locker_transactions()
        return res
