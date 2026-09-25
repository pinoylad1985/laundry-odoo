import re

from odoo import api, fields, models


def phone_last10(value):
    """The comparison key for a phone number: its last 10 digits.

    Locker customers key their own number into the PudoPro screen, so what
    arrives can be `09171234567`, `+63 917 123 4567` or `9171234567` for the
    same person. The last 10 digits are the part that identifies a PH mobile
    (the subscriber number without the 0 or +63), so that is what both sides of
    the match are reduced to.
    """
    digits = re.sub(r'\D', '', value or '')
    return digits[-10:] if len(digits) >= 10 else ''


class LaundryLockerTransaction(models.Model):
    _name = 'laundry.locker.transaction'
    _description = 'Locker Transaction'
    _rec_name = 'ref'
    _order = 'created_at desc, ref desc'

    # --- identity -------------------------------------------------------
    # `ref` is PudoPro's Reference_Number, which is also the Firebase key under
    # /orders. Every sync upserts on it, so it has to be unique.
    ref = fields.Char(string='Ref #', required=True, index=True, copy=False)

    location_code = fields.Char(string='Location Code', index=True)
    location_name = fields.Char(string='Location')

    # --- customer as the locker recorded them ---------------------------
    customer_name = fields.Char(string='Customer')
    phone = fields.Char(string='Phone')
    # Indexed so the partner match is an equality hit rather than a scan over
    # every transaction.
    phone_last10 = fields.Char(
        string='Phone Key', compute='_compute_phone_last10', store=True, index=True
    )

    # What the locker actually sent. Kept so a later sync can tell a value
    # nobody touched from one a cashier corrected, and only overwrite the
    # former - otherwise the hourly pull would put the customer's typo back.
    source_name = fields.Char(string='Source Customer', copy=False)
    source_phone = fields.Char(string='Source Phone', copy=False)

    # --- what they dropped off ------------------------------------------
    service = fields.Char(string='Service')
    turnaround = fields.Char(string='Turnaround')
    dirty_door = fields.Char(string='Dirty Door')

    # The slot the customer chose at the locker: pickup = the bag leaving the
    # locker, delivery = the clean laundry coming back. Carried into the New
    # Order modal when the drop-off is billed, so the till schedules what the
    # customer was promised instead of a cashier re-picking it from memory.
    pickup_datetime = fields.Datetime(string='Pickup', index=True)
    delivery_datetime = fields.Datetime(string='Delivery', index=True)

    # Both written straight from the feed: /orders carries the DASHBOARD's
    # workflow state (`status` / `overallStatus`), already in words, not
    # PudoPro's numeric codes - so there is nothing left to map.
    status_code = fields.Char(string='Status Code', index=True)
    status_label = fields.Char(string='Status', index=True)

    created_at = fields.Datetime(string='Created', index=True)
    updated_at = fields.Datetime(string='Updated')
    # When the bag actually became laundry. /orders only gains a row once
    # PudoPro has moved the transaction to New Laundry, so this is its
    # createdAt - which is why an online booking made the day before shows the
    # moment the bag went into the door, not the moment it was booked.
    new_laundry_at = fields.Datetime(string='New Laundry', index=True)

    # --- the customer book match ----------------------------------------
    partner_id = fields.Many2one(
        'res.partner', string='Matched Customer', ondelete='set null',
        compute='_compute_partner_id', store=True, readonly=False,
        help="Matched on the last 10 digits of the phone number. Can be set by "
             "hand when the locker's number was keyed in wrong.",
    )
    customer_match = fields.Selection(
        [('returning', 'Returning'), ('new', 'New')],
        string='Customer Match', compute='_compute_customer_match', store=True, index=True,
    )
    # A locker customer types their own number and gets it wrong often enough
    # that a contact must not be created off an unverified one - a bad number
    # poisons every future match. Set when someone has called it and heard it ring.
    phone_verified = fields.Boolean(
        string='Number Verified', copy=False,
        help="Ticked once the number has been called and confirmed ringing.",
    )

    # --- billing ---------------------------------------------------------
    # Set when a POS order carrying this ref is VALIDATED, not when a till
    # picks it: until a sale is rung up the drop-off is nobody's, and stays in
    # every till's picker. Cleared again if that sale is refunded.
    billed = fields.Boolean(string='Billed', index=True, copy=False)
    billed_date = fields.Datetime(string='Billed On', copy=False)
    pos_order_id = fields.Many2one(
        'pos.order', string='POS Order', ondelete='set null', copy=False
    )

    synced_at = fields.Datetime(string='Last Synced')

    # Odoo 19: _sql_constraints is ignored (it only logs a warning), so the
    # uniqueness the sync upsert relies on has to be declared this way.
    _ref_uniq = models.Constraint(
        'unique(ref)',
        'A locker transaction with this Ref # already exists.',
    )

    @api.depends('phone')
    def _compute_phone_last10(self):
        for tx in self:
            tx.phone_last10 = phone_last10(tx.phone)

    @api.depends('phone_last10')
    def _compute_partner_id(self):
        """Match the locker's number against the customer book.

        Only recomputed from the phone: a partner set by hand (after a wrong
        number was corrected) must survive, which is what `readonly=False` on a
        stored compute buys. A partner created LATER is picked up by the hook in
        res_partner.py, not here.
        """
        for tx in self:
            key = tx.phone_last10
            if not key:
                tx.partner_id = False
                continue
            tx.partner_id = self.env['res.partner'].search(
                [('laundry_phone_last10', '=', key)], limit=1, order='id'
            )

    @api.depends('partner_id')
    def _compute_customer_match(self):
        for tx in self:
            tx.customer_match = 'returning' if tx.partner_id else 'new'

    # --- actions ---------------------------------------------------------
    def action_mark_verified(self):
        self.write({'phone_verified': True})

    def action_rematch_partner(self):
        """Re-run the phone match - for after a number is corrected by hand."""
        self._compute_partner_id()
        self._compute_customer_match()
