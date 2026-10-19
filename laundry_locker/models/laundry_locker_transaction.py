import re

from odoo import api, fields, models

# The key is laundry_pos's: the customer book and this feed have to reduce
# a number the SAME way, or a transaction matches a customer that the
# duplicate-phone guard would never have allowed to exist - and the two
# rules drift apart silently. Re-exported from here because this module's
# own readers import it from this file.
from odoo.addons.laundry_pos.models.res_partner import phone_last10  # noqa: F401


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


def _flat_name(value):
    """A name with its spacing collapsed - the only difference ignored.

    CASE is NOT ignored: two spellings of one number are the same number, two
    spellings of one name are not the same text. `Mary Smith` retyped as
    `MARY SMITH` IS a correction, and the column exists to say what was
    changed.
    """
    return ' '.join((value or '').split())


def _digits(value):
    return re.sub(r'\D', '', value or '')


def corrected_phone(value, original):
    """`value` as a correction of `original` - or False if it corrects nothing.

    Compared on DIGITS, not on the string: `0917 555 0002` and `09175550002`
    are one number, and storing the second as a correction of the first would
    invent a correction out of spacing. The same reasoning as phone_last10, on
    the whole number rather than its last ten - this asks whether somebody
    CHANGED it, not whether two records are the same customer.
    """
    corrected = (ph_local_phone((value or '').strip()) or '').strip()
    if not corrected:
        return False
    if _digits(corrected) == _digits(ph_local_phone(original) or ''):
        return False
    return corrected


def corrected_name(value, original):
    """`value` as a correction of `original` - or False if it corrects nothing."""
    corrected = (value or '').strip()
    if not corrected or _flat_name(corrected) == _flat_name(original):
        return False
    return corrected


class LaundryLockerTransaction(models.Model):
    _name = 'laundry.locker.transaction'
    _description = 'Locker Transaction'
    _rec_name = 'ref'
    _order = 'created_at desc, ref desc'

    # --- identity -------------------------------------------------------
    # `ref` is PudoPro's Reference_Number, which is also the Firebase key under
    # /orders. Every sync upserts on it, so it has to be unique.
    ref = fields.Char(string='Ref #', required=True, index=True, copy=False)

    # A drop-off DELETED at the PudoPro end. Archived rather than deleted here
    # for two reasons: the feed is upserted, never reconciled, so a row deleted
    # in Odoo is simply rebuilt by the next Full resync; and a drop-off that was
    # listed and then withdrawn is a thing that happened, which a Billed row in
    # particular must keep saying.
    #
    # It is `active` and not a flag of our own precisely BECAUSE Odoo filters
    # it out of every search by default - which takes the row out of the
    # cashier's locker picker and out of the partner re-match without either
    # having to name it. (A CLEAR is a different thing: the feed reports it as
    # a status, so `get_unbilled_for_pos` filters on `status_label` and the row
    # stays visible - a deletion can only ever be recorded here.)
    active = fields.Boolean(
        string='Active', default=True, index=True, copy=False,
        help="Unticked when the drop-off was deleted at the locker end. The row "
             "stays for the record but drops out of the POS locker picker.",
    )

    location_code = fields.Char(string='Location Code', index=True)
    location_name = fields.Char(string='Location')

    # --- customer as the locker recorded them ---------------------------
    # THE LOCKER'S OWN, and never written over. What the customer keyed into
    # the PudoPro screen is what is printed on their drop-off and what they
    # quote when they ring up about it, so a correction is kept BESIDE it
    # rather than over it. The sync owns this pair and refreshes it from the
    # feed on every pull; the form shows it read-only.
    customer_name = fields.Char(string='Customer')
    phone = fields.Char(string='Phone')

    # --- the shop's correction, where there is one ----------------------
    # Blank unless something was actually changed: a value that only re-spaces
    # the locker's own is stored as blank (corrected_name / corrected_phone
    # above, applied on every write), so "this drop-off was corrected" and
    # "this field has something in it" are the same question - which is what
    # lets the register and the list print an em-dash rather than repeat the
    # number in both columns.
    #
    # This pair is what the REGISTER works from - the number it calls and
    # matches, the name a contact is created under - through
    # _laundry_phone_in_force / _laundry_name_in_force, which read the
    # correction and fall back to the locker's own. Spelled the same way here,
    # on the picker's rows and in both apps' columns, so one fact has one name
    # wherever it is read.
    customer_name_corrected = fields.Char(string='Customer (Corrected)', copy=False)
    phone_corrected = fields.Char(string='Phone (Corrected)', copy=False)

    # Indexed so the partner match is an equality hit rather than a scan over
    # every transaction. Keyed on the number IN FORCE, so a drop-off matches
    # the customer it was corrected onto and not the one it was keyed in as.
    phone_last10 = fields.Char(
        string='Phone Key', compute='_compute_phone_last10', store=True, index=True
    )

    # --- what they dropped off ------------------------------------------
    service = fields.Char(string='Service')
    turnaround = fields.Char(string='Turnaround')
    dirty_door = fields.Char(string='Dirty Door')

    # The slot the customer chose at the locker: pickup = the bag leaving the
    # locker, delivery = the clean laundry coming back. Carried into the New
    # Order modal when the drop-off is billed, so the register schedules what the
    # customer was promised instead of a cashier re-picking it from memory.
    pickup_datetime = fields.Datetime(string='Pickup', index=True)
    delivery_datetime = fields.Datetime(string='Delivery', index=True)

    # Both written straight from the feed: /orders carries the DASHBOARD's
    # workflow state (`status` / `overallStatus`), already in words, not
    # PudoPro's numeric codes - so there is nothing left to map.
    status_code = fields.Char(string='Status Code', index=True)
    # The feed's own wording, and LOAD-BEARING: `Cleared` here is what takes a
    # drop-off out of the POS locker picker and out of the To Bill filter. It is
    # matched exactly (see CLEARED_STATUS in laundry_locker_sync), so if PudoPro
    # ever rewords the status, cleared drop-offs start offering themselves to be
    # billed again - that is the first place to look. Indexed because both of
    # those filters name it.
    status_label = fields.Char(string='Status', index=True)

    created_at = fields.Datetime(string='Created', index=True)
    updated_at = fields.Datetime(string='Updated')
    # When the bag actually became laundry. /orders only gains a row once
    # PudoPro has moved the transaction to New Laundry, so this is its
    # createdAt - which is why an online booking made the day before shows the
    # moment the bag went into the door, not the moment it was booked.
    new_laundry_at = fields.Datetime(string='New Laundry', index=True)

    # --- the customer book match ----------------------------------------
    # readonly=False so the REGISTER can write it (the contact created from a
    # verified number in laundry_locker_pos), not so a person can pick it: the
    # form shows this field read-only. The customer on a transaction has to be
    # one the phone number earned - either it matched, or the number was called
    # and confirmed before a contact was made from it. To correct a mistyped
    # number, fix `phone` and the match re-runs on its own.
    partner_id = fields.Many2one(
        'res.partner', string='Matched Customer', ondelete='set null',
        compute='_compute_partner_id', store=True, readonly=False,
        help="Matched on the last 10 digits of the phone number. To change it, "
             "correct the phone number - the match re-runs by itself.",
    )
    customer_match = fields.Selection(
        [('returning', 'Returning'), ('new', 'New')],
        string='Customer Match', compute='_compute_customer_match', store=True, index=True,
    )
    # --- the matched contact as one cell, the way POS Orders shows one -----
    # Name, phone and address stacked in a single column, drawn by
    # laundry_pos's `laundry_customer_block` widget - the same column that list
    # carries, so a counter moving between the two reads them alike.
    #
    # These read the CONTACT, so they fill in on a returning row only. The
    # locker's own customer_name / phone are separate columns and always there:
    # this cell is the customer book's answer, not a second copy of the
    # question.
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
        string='Customer Details', compute='_compute_laundry_customer_details',
        help="The contact this drop-off was matched to, with the phone number "
             "and address on file. Blank on a new customer - see the Customer "
             "and Phone columns for what was keyed in at the locker.",
    )

    # A locker customer types their own number and gets it wrong often enough
    # that a contact must not be created off an unverified one - a bad number
    # poisons every future match. Set when someone has called it and heard it ring.
    phone_verified = fields.Boolean(
        string='Number Verified', copy=False,
        help="Ticked once the number has been called and confirmed ringing.",
    )

    # The contact this drop-off CREATED, as opposed to the one it matched.
    #
    # It is the difference between a record that is ours to correct and one
    # that is somebody else's. A number called, confirmed and claimed makes a
    # contact on the cashier's word; when that word turns out to be wrong, the
    # correction has to be able to reach back to the record it caused. Without
    # this field it cannot tell them apart - `partner_id` looks identical
    # whether the contact was found or made - so a correction left the first
    # contact behind in the book, holding the wrong number and (under the
    # one-customer-per-number rule) squatting on it, where the real owner of
    # that number could never be added.
    #
    # Kept even after the contact stops being the match, which is the whole
    # point: it is read when `partner_id` has already moved on.
    created_partner_id = fields.Many2one(
        'res.partner', string='Contact Created Here', copy=False,
        ondelete='set null', index=True,
        help="The contact this drop-off created from its phone number, rather "
             "than matched. Correcting the number corrects that contact "
             "instead of leaving it behind.",
    )

    # --- billing ---------------------------------------------------------
    # Set when a POS order carrying this ref is VALIDATED, not when a register
    # picks it: until a sale is rung up the drop-off is nobody's, and stays in
    # every register's picker. Cleared again if that sale is refunded.
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

    # ------------------------------------------------------------------
    # the value in force
    # ------------------------------------------------------------------
    # The correction where there is one, the locker's own where there is not.
    # Two methods rather than two more fields: the columns are a PAIR per
    # fact - what the customer typed and what the shop put in its place - and
    # a third column reading "whichever of those two applies" is one nobody
    # can act on. The picker's JS draws the same line in
    # static/src/utils/locker_rows.js; the two have to stay in step.
    def _laundry_phone_in_force(self):
        """The number this drop-off is actually called and matched on."""
        self.ensure_one()
        return ph_local_phone(self.phone_corrected or self.phone) or ''

    def _laundry_name_in_force(self):
        """The name a contact made from this drop-off is created under."""
        self.ensure_one()
        return (self.customer_name_corrected or self.customer_name or '').strip()

    # Inlined rather than calling _laundry_phone_in_force: a compute runs over
    # a recordset and that method answers for one record.
    @api.depends('phone', 'phone_corrected')
    def _compute_phone_last10(self):
        for tx in self:
            tx.phone_last10 = phone_last10(tx.phone_corrected or tx.phone)

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

    @api.depends('partner_id', 'partner_id.phone',
                 'partner_id.street', 'partner_id.street2')
    def _compute_laundry_customer_details(self):
        """The matched contact's own name, number and address.

        All three come off the CONTACT and nothing falls back to the locker's
        copy: an unmatched row is meant to read blank here. Dressing the keyed-in
        name up in this cell would look exactly like a match that was never
        made, and the name and number as keyed are their own two columns anyway.
        """
        for tx in self:
            partner = tx.partner_id
            tx.customer_block = partner.display_name or False
            tx.laundry_customer_phone = partner.phone or False
            tx.laundry_customer_address = ' '.join(
                filter(None, [partner.street, partner.street2])
            ).strip() or False

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

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def _laundry_has_corrections(self, vals):
        return 'phone_corrected' in vals or 'customer_name_corrected' in vals

    def _laundry_clean_corrections(self, vals, current=None):
        """Blank a "correction" that corrects nothing.

        The register sends the two boxes back on EVERY claim, touched or not,
        so without this the first claim on any drop-off would record its own
        number as a correction of itself and every row in the book would read
        as corrected. The backend form can do the same by hand.

        `current` is the pair the correction is judged against - the record's
        own, or the vals of a row being created.
        """
        if not self._laundry_has_corrections(vals):
            return vals
        if current is None:
            self.ensure_one()
            current = {'customer_name': self.customer_name, 'phone': self.phone}
        cleaned = dict(vals)
        if 'phone_corrected' in cleaned:
            cleaned['phone_corrected'] = corrected_phone(
                cleaned['phone_corrected'], vals.get('phone', current.get('phone'))
            )
        if 'customer_name_corrected' in cleaned:
            cleaned['customer_name_corrected'] = corrected_name(
                cleaned['customer_name_corrected'],
                vals.get('customer_name', current.get('customer_name')),
            )
        return cleaned

    def _laundry_new_phone_key(self, vals):
        """The last-10 key this write would leave the row standing on."""
        self.ensure_one()
        corrected = (
            vals['phone_corrected'] if 'phone_corrected' in vals
            else self.phone_corrected
        )
        original = vals['phone'] if 'phone' in vals else self.phone
        return phone_last10(corrected or original) or ''

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([
            self._laundry_clean_corrections(vals, vals) for vals in vals_list
        ])

    def write(self, vals):
        """A number that CHANGES is a number nobody has called.

        `phone_verified` is a statement about ONE number - somebody dialed it
        and heard it ring. Carried across onto a different number it says
        nothing at all, and would let a drop-off whose number was corrected
        (from the backend form, or by a later feed update) be claimed at the
        register with no call ever made.

        Cleared only when the digits actually move, and judged on the number
        IN FORCE - a correction arriving, changing or being cleared all move
        it, while the feed re-sending the same locker number does not. Without
        that last part every row would un-verify itself every five minutes.

        An explicit `phone_verified` in the same call wins - that is
        `pos_claim` recording the call the cashier just made ON the corrected
        number, which is the one case where the two travel together.
        """
        if len(self) > 1 and self._laundry_has_corrections(vals):
            # "Does this correct anything?" is asked against THAT row's own
            # locker value, so a correction written across several rows at
            # once is split rather than judged against one of them. Nothing in
            # this module writes one that way; an import could.
            for tx in self:
                tx.write(vals)
            return True
        if len(self) == 1:
            vals = self._laundry_clean_corrections(vals)
        changed = self.browse()
        if 'phone_verified' not in vals and (
            'phone' in vals or 'phone_corrected' in vals
        ):
            # Read BEFORE the write, while phone_last10 still holds the old key.
            changed = self.filtered(
                lambda t: (t.phone_last10 or '') != t._laundry_new_phone_key(vals)
            )
        result = super().write(vals)
        if changed:
            # A plain write, not a recursive one: neither phone field is in
            # these vals, so it takes neither branch above and stops.
            changed.phone_verified = False
        return result
