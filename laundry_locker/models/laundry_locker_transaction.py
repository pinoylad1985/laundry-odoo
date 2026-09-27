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


def ph_local_phone(value):
    """A phone number written the way the shop writes it: 09xx xxx xxxx.

    PudoPro hands back the subscriber number on its own - `9171234567` - and a
    number with no leading 0 does not read as a phone number to anyone here,
    on a receipt or on a contact. The trunk 0 is put back.

    ONLY on a 10-digit mobile. A number that already has its 0, one in +63
    form, and a landline are all left exactly as they came: guessing at
    anything else would mangle numbers that were never broken.
    """
    text = (value or '').strip()
    if not text:
        return value
    digits = re.sub(r'\D', '', text)
    if len(digits) == 10 and digits.startswith('9'):
        return '0' + digits
    return value


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
    # --- the customer as one cell, the way the POS Orders list shows one --
    # Name, phone and address stacked in a single column, drawn by
    # laundry_pos's `laundry_customer_block` widget - the same column that list
    # carries, so a counter moving between the two reads them alike.
    #
    # The two field names below are NOT ours to choose: that widget names them
    # as its fieldDependencies. Rename either one here and half the cell goes
    # blank, with nothing on screen to say why.
    laundry_customer_phone = fields.Char(
        string='Customer Phone', compute='_compute_laundry_customer_details',
    )
    laundry_customer_address = fields.Char(
        string='Customer Address', compute='_compute_laundry_customer_details',
    )
    # Not stored, for the same reason laundry_pos does not store its own: it is
    # a display column, and storing it would bump write_date on every row every
    # time a customer's address is edited.
    customer_block = fields.Char(
        string='Customer', compute='_compute_laundry_customer_details',
        help="Who this drop-off belongs to: the matched contact where there is "
             "one, otherwise the name keyed in at the locker.",
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
    # Both copied off the order and STORED, so the locker list can be read,
    # searched and grouped on them without opening the order - and so the two
    # numbers are never mistaken for each other. The ORDER number is the one
    # the counter calls out and the one printed big on the receipt, and it is
    # NOT unique: it restarts with every session. The RECEIPT number is.
    #
    # Computed, not related: a stored related is writable, and a stray write
    # here would travel back onto the pos.order.
    pos_order_number = fields.Char(
        string='Order #', compute='_compute_pos_order_numbers', store=True, index=True,
        help="Order number of the sale that billed this drop-off. Not unique.",
    )
    pos_order_ref = fields.Char(
        string='Receipt #', compute='_compute_pos_order_numbers', store=True, index=True,
        help="Receipt number of the sale that billed this drop-off. Unique.",
    )

    # Who rang the sale up, and who took the bag off the customer. Stored for
    # the same reason as the two numbers above - this list is read and grouped
    # without opening the order - and Char rather than a link for the rider,
    # because that is a Char on the order too (a name captured at sign-off).
    pos_cashier = fields.Char(
        string='Cashier', compute='_compute_pos_order_staff', store=True,
        help="Cashier who rang up the sale that billed this drop-off.",
    )
    pos_pickup_rider = fields.Char(
        string='Pickup Rider', compute='_compute_pos_order_staff', store=True,
        help="Rider who signed off collecting this drop-off, read off the sale "
             "it was billed on. Blank until a rider has signed for it.",
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

    @api.depends('pos_order_id.tracking_number', 'pos_order_id.pos_reference')
    def _compute_pos_order_numbers(self):
        for tx in self:
            tx.pos_order_number = tx.pos_order_id.tracking_number or False
            tx.pos_order_ref = tx.pos_order_id.pos_reference or False

    @api.depends('pos_order_id', 'pos_order_id.user_id',
                 'pos_order_id.laundry_pickup_rider')
    def _compute_pos_order_staff(self):
        """Name the cashier who rang the sale, and the rider who collected."""
        # `employee_id` is the cashier pos_hr puts on the order, and laundry_pos
        # depends on pos_hr - but it is read through _fields rather than named
        # in @api.depends. A depends on a field that turns out not to be there
        # fails the whole module at load, and a display column is not worth
        # risking an upgrade over. The trade is that a cashier CHANGED on an
        # already-linked order does not refresh here, which is not something
        # that happens once a sale is validated.
        has_employee = 'employee_id' in self.env['pos.order']._fields
        for tx in self:
            order = tx.pos_order_id
            employee = order.employee_id.name if has_employee else False
            # Falls back to the session's own user, so the column names
            # somebody even on a register nobody switched cashiers on.
            tx.pos_cashier = employee or order.user_id.name or False
            tx.pos_pickup_rider = order.laundry_pickup_rider or False

    @api.depends('partner_id', 'partner_id.street', 'partner_id.street2',
                 'customer_name', 'phone')
    def _compute_laundry_customer_details(self):
        for tx in self:
            partner = tx.partner_id
            # The LOCKER's number, matched or not: it is the one the customer
            # keyed in and the one this row was matched on. A contact's other
            # number would not be the one written on this bag.
            tx.laundry_customer_phone = tx.phone or False
            tx.laundry_customer_address = ' '.join(
                filter(None, [partner.street, partner.street2])
            ).strip() or False
            # Falls back to the keyed-in name, so an unmatched row still says
            # who it is rather than showing a bare phone number. Which of the
            # two is on screen reads off Customer Match, right beside it.
            tx.customer_block = partner.display_name or tx.customer_name or False

    @api.depends('partner_id')
    def _compute_customer_match(self):
        for tx in self:
            tx.customer_match = 'returning' if tx.partner_id else 'new'

    def _laundry_rematch_partner(self):
        """Re-run the phone match against the customer book.

        Internal, not a button: a number corrected on this form recomputes the
        match by itself (partner_id depends on it). This is for the other
        direction - res_partner's hook, when the CUSTOMER turns up after the
        transaction did.
        """
        self._compute_partner_id()
        self._compute_customer_match()
