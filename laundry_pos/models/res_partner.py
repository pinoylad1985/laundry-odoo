import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


def phone_last10(value):
    """The comparison key for a phone number: its last 10 digits.

    The same person writes their number down as `09171234567`, `+63 917 123
    4567` or `917 123 4567` on different days, and a cashier re-typing it at
    the counter spells it a fourth way. The last 10 digits are the part that
    identifies a PH mobile (the subscriber number, without the trunk 0 or the
    +63), so that is what the customer book is keyed on - the same key
    laundry_locker matches its transactions to a customer on.
    """
    digits = re.sub('[^0-9]', '', value or '')
    return digits[-10:] if len(digits) >= 10 else ''


# How many DIFFERENT digits a real number has to contain. A PH mobile opens
# `09`, so the remaining nine digits have to bring at least two more between
# them - which every real number does and `09111111111` does not. Four rather
# than five because `0909 999 9999` is the kind of number somebody pays to
# own, and refusing a real number is worse than letting one filler through.
PHONE_MIN_UNIQUE_DIGITS = 4


def is_placeholder_phone(value):
    """True when a "number" is obviously not one - a filler typed to get past
    a required field.

    `00000000000` is the one in the book (seven contacts carry it), from the
    days when the register asked every walk-in for a number whether the shop
    needed one or not. A filler is worse than a blank, because a blank is
    honest and a filler MATCHES: the next walk-in saved under the same zeros
    joins a stranger's order history, and a number the shop needs to call is
    satisfied by digits that ring nowhere.

    Deliberately narrow - a rule that guesses would start refusing real
    numbers, and a refused real number is a customer turned away. Three shapes
    are certain:

    * one digit repeated (`00000000000`);
    * **fewer than PHONE_MIN_UNIQUE_DIGITS different digits in it** - a number
      built out of three digits or fewer is somebody leaning on the keypad
      (`09111111111`, `09090909090`, `09999900000`). An eleven-digit number
      can be a filler just as easily as a short one, which is what the digit
      count on its own misses;
    * a straight run up or down (`1234567890`, `0987654321`).

    It will not catch a cashier who invents `09171111112` (five different
    digits), and it is not meant to: the real fix is that the number is no
    longer asked for where the shop does not need it.
    """
    digits = re.sub('[^0-9]', '', value or '')
    if not digits:
        return False
    # Judged on the whole string AND on its last 10 digits - the same
    # identifying part the duplicate rule is keyed on - so a filler does not
    # slip through behind a country code (`+63 000 000 0000`).
    for run in {digits, phone_last10(digits)} - {''}:
        if len(set(run)) == 1:
            return True
        if len(run) >= 6:
            # The variety test needs enough digits to be a judgement rather
            # than an accident: three different digits out of four is a
            # perfectly ordinary thing, out of eleven it is not. Same floor
            # the run test uses below.
            if len(set(run)) < PHONE_MIN_UNIQUE_DIGITS:
                return True
            # Counted modulo 10 so the 9-to-0 wrap in `1234567890` still
            # reads as one step up - it is the commonest run of the lot.
            steps = {(int(b) - int(a)) % 10 for a, b in zip(run, run[1:])}
            if steps in ({1}, {9}):
                return True
    return False


# A mobile number in the Philippines is 11 digits (`09171234567`); written
# with the country code it is 12 (`+639171234567`). So 11 is the floor, and
# the one number length the shop actually deals in.
PHONE_MIN_DIGITS = 11

# What a phone number is allowed to be made of: digits, and the characters
# people separate them with. Everything else - letters above all - is refused,
# because this field is read by somebody about to dial it.
PHONE_ALLOWED_RE = re.compile(r'[^0-9+()./\s-]')


def phone_problem(value):
    """Why this is not a usable phone number: a short code, or None.

    One entry point for every way a number can be wrong, in the order the
    tests get less obvious: `'letters'` (characters a phone does not have),
    `'short'` (fewer than PHONE_MIN_DIGITS digits), `'placeholder'` (a filler -
    see is_placeholder_phone). The caller turns the code into a sentence; the
    code itself is kept free of wording so the register, the locker and the
    server refusal can all print the SAME sentence.

    A BLANK is not a problem. It is the honest answer when the shop has no
    number for somebody, and the one the phone rules deliberately leave open -
    a number is only required where the laundry leaves the counter.
    """
    raw = (value or '').strip()
    if not raw:
        return None
    if PHONE_ALLOWED_RE.search(raw):
        return 'letters'
    if len(re.sub('[^0-9]', '', raw)) < PHONE_MIN_DIGITS:
        return 'short'
    if is_placeholder_phone(raw):
        return 'placeholder'
    return None


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Comma-joined tag names, computed on the partner itself so the POS receipt
    # can show them WITHOUT loading the res.partner.category model. Relational
    # loading only pulls categories referenced by pre-loaded partners, so tags
    # on customers fetched on-demand (cashier search) would otherwise be missing.
    pos_tag_names = fields.Char(
        string="POS Tag Names",
        compute="_compute_pos_tag_names",
    )

    @api.depends('category_id', 'category_id.name')
    def _compute_pos_tag_names(self):
        for partner in self:
            partner.pos_tag_names = ", ".join(partner.category_id.mapped('name'))

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if 'pos_tag_names' not in fields_list:
            fields_list.append('pos_tag_names')
        return fields_list

    # --- One customer per phone number -----------------------------------
    #
    # A second contact for the same person splits their orders, their dues and
    # their history across two records, and nothing afterwards puts them back
    # together: the counter sees half a customer whichever one it opens. The
    # number is the one thing a laundry customer always gives, so it is what
    # the book is kept unique on.

    def _laundry_phone_twin(self, phone, exclude_id=None):
        """The partner that already holds this number, or an empty recordset.

        Matched in SQL on the DIGITS of the stored number rather than through
        the ORM, because the book holds the same number in several spellings
        and no domain can compare them: `0917 123 4567` and `+639171234567`
        are one number, and only stripping both to digits says so.

        ARCHIVED partners count. An archived duplicate is invisible to the
        cashier's search, so skipping them is exactly how a second record gets
        created for someone who is already in the book - the message below
        says when the twin is archived so the cashier knows to restore it
        rather than hunt for something they cannot see.

        CHILD contacts (a delivery address under a company) are left out on
        both sides: they legitimately carry their parent's number, and
        blocking that would stop addresses being added at all.
        """
        key = phone_last10(phone)
        if not key:
            return self.browse()
        query = """
            SELECT id
              FROM res_partner
             WHERE parent_id IS NULL
               AND regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g') LIKE %s
        """
        params = ['%' + key]
        if exclude_id:
            query += " AND id != %s"
            params.append(exclude_id)
        query += " ORDER BY id LIMIT 1"
        self.env.cr.execute(query, params)
        row = self.env.cr.fetchone()
        # sudo for the name only: the twin may sit outside what this user is
        # allowed to read, and "it exists but I cannot tell you which" is not
        # an answer anyone can act on.
        return self.env['res.partner'].sudo().browse(row[0]) if row else self.browse()

    def _laundry_contact_block(self, with_tags=False):
        """A contact the way the POS Orders list shows one: name, then phone,
        then address, each on its own line.

        The refusal below printed the name alone until v1.5.12, which is not
        enough to act on - the book holds several people called MARY, and a
        cashier told only "already used by MARY" has been told no and nothing
        else. The phone is what the rule matched on and the address is what
        tells two people of the same name apart, so both belong in the answer.

        Same three lines, in the same order, as `laundry_customer_block` on
        pos.order. Deliberately: the cashier has already learned to read that
        block in the Orders list.

        `with_tags` adds a FOURTH line, under the address, and is off by
        default so the places that only have to say WHICH customer this is
        still print the same three. It is on in the locker picker, where the
        tag is not background: a Locker order requires one
        (CONTACT_REQUIREMENTS), so whether the matched customer already
        carries a tag decides whether the order can be taken at all. It says
        "No tag" rather than dropping the line, because a line that is simply
        absent cannot be told apart from one nobody printed.
        """
        self.ensure_one()
        address = ' '.join(filter(None, [self.street, self.street2])).strip()
        lines = [line for line in (self.display_name, self.phone, address) if line]
        if with_tags:
            lines.append(self.pos_tag_names or _("No tag"))
        return lines

    @api.model
    def laundry_phone_twin_info(self, phone, exclude_id=None):
        """Who already holds this number - asked BEFORE a save, not after.

        The server rule below refuses; this is what lets the register and the
        contact form ASK first, so they can offer that customer instead of
        rejecting work the cashier has already typed. One method for both, so
        the two screens can never disagree about who the twin is.

        Returns False when the number is free. A number the shape rules
        REFUSE is reported separately, as `{'refused': True, 'message': ...}`:
        there is no twin to offer behind it, only a number to fix, and the
        message is the server's own so the dialog cannot word the rule
        differently from the refusal.
        """
        message = self.laundry_phone_problem_message(phone)
        if message:
            return {'refused': True, 'message': message}
        twin = self._laundry_phone_twin(phone, exclude_id=exclude_id)
        if not twin:
            return False
        # Already sudo - see _laundry_phone_twin.
        return {
            'refused': False,
            'id': twin.id,
            'name': twin.display_name,
            'lines': twin._laundry_contact_block(),
            'archived': not twin.active,
        }

    @api.model
    def laundry_restore_phone_twin(self, partner_id):
        """Put an archived twin back in the book, so it can be used.

        The duplicate dialog used to refuse an archived twin outright and say
        to restore it elsewhere, on the reasoning that it was taken out of the
        book deliberately. In the shop that reads as a dead end: the cashier
        has a customer in front of them, has been shown that customer's own
        record, and is being sent to find somebody with backend access. The
        owner's call is to let them restore it from there - the alternative is
        a SECOND contact for the same person, which is the exact thing this
        whole rule exists to prevent.

        NOT sudo: restoring is the cashier's own action, taken on their own
        rights, and it is written in the record's history as theirs. Reading
        the twin's NAME is sudo (see _laundry_phone_twin) because a cashier
        cannot act on "it exists but I cannot tell you which one"; that
        reasoning does not stretch to writing.
        """
        partner = self.browse(int(partner_id)).exists()
        if not partner:
            raise UserError(_("That contact no longer exists."))
        if not partner.active:
            partner.active = True
        return True

    @api.model
    def laundry_phone_problem_message(self, phone):
        """What to tell whoever typed this number, or '' when it is fine.

        ONE wording, built here and used by all three places that ask: the
        server refusal below, the register's twin dialog
        (laundry_phone_twin_info) and the locker picker's Check button
        (laundry.locker.transaction.pos_check_phone). A cashier refused at the
        counter and then refused again on the contact form should be reading
        the same sentence both times - two wordings of one rule read as two
        rules, and invite a second guess about which one is really in force.

        TWO SHORT LINES: what is wrong, then what to do. It was a paragraph
        until v1.5.19 - the problem, the reasoning behind the rule, and where
        a number is required - and the owner's call is that nobody reads a
        paragraph at a counter with a customer waiting. The reasoning lives in
        CLAUDE.md, where it is read once by whoever maintains the rule, rather
        than every time somebody mistypes a number.

        It still names the way out, because "that is wrong" is not an
        instruction - but only the way out of THIS: enter a number that is
        one. It said "or leave it blank" until v1.5.20; the owner's call is
        that where a number is optional is something a cashier learns from
        the New Order modal, which asks for one on exactly three service
        types and does not on the other two. A refusal is not the place to
        teach a rule it is not enforcing.
        """
        problem = phone_problem(phone)
        if not problem:
            return ''
        tail = _("Enter a valid number.")
        if problem == 'letters':
            head = _(
                "%(phone)s is not a phone number - a phone number is digits "
                "only.",
                phone=phone,
            )
        elif problem == 'short':
            head = _(
                "%(phone)s is too short - a mobile number has %(minimum)s "
                "digits (09171234567).",
                phone=phone,
                minimum=PHONE_MIN_DIGITS,
            )
        else:
            head = _(
                "%(phone)s does not look like a real phone number.",
                phone=phone,
            )
        return '\n'.join((head, tail))

    def _laundry_check_phone(self, phone, exclude_id=None):
        # Refused outright, and BEFORE the duplicate check, which would
        # otherwise answer a number that is not one with a stranger's name
        # ("already used by STEVEN") - true, useless, and nothing the cashier
        # can act on.
        message = self.laundry_phone_problem_message(phone)
        if message:
            raise ValidationError(message)
        twin = self._laundry_phone_twin(phone, exclude_id=exclude_id)
        if not twin:
            return
        # Already sudo - see _laundry_phone_twin.
        details = twin._laundry_contact_block()
        if not twin.active:
            details.append(_('(archived - restore it to use it)'))
        raise ValidationError(_(
            "This phone number is already used by:\n\n%(details)s\n\n"
            "Open that customer instead of creating a new one - two contacts "
            "for one person split their orders, points and dues between them.",
            details='\n'.join(details),
        ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('phone') and not vals.get('parent_id'):
                self._laundry_check_phone(vals['phone'])
        return super().create(vals_list)

    def write(self, vals):
        # Checked on write as well, or the block is a formality: creating the
        # customer with no number and typing it in a second later would walk
        # straight past it. Only when the number itself is being set, and
        # never against the record's own number.
        if vals.get('phone'):
            new_key = phone_last10(vals['phone'])
            for partner in self:
                # A save that does not actually CHANGE the number is waved
                # through. The POS customer form re-sends `phone` whatever was
                # touched, so without this the seven contacts already holding
                # `00000000000` could never be edited again - not even to give
                # them a real number - and the rule would be punishing a
                # cashier for a value somebody typed years ago.
                #
                # Judged on the LAST 10 DIGITS, the one key this module
                # compares numbers on anywhere. `0917 123 4567` and
                # `+639171234567` are one number, so retyping one as the other
                # is not a change and nothing should be asked about it. Judged
                # on every digit instead (as it was until v1.5.13) the two read
                # as different numbers, which is the opposite of what the
                # duplicate rule itself says.
                #
                # Both sides must HAVE a key: a number too short for one gives
                # '' on either side, and treating '' as a match would wave a
                # change from one short number to another straight past the
                # filler check below.
                if new_key and phone_last10(partner.phone) == new_key:
                    continue
                # And so is a save that re-sends the stored value CHARACTER
                # FOR CHARACTER. The test above cannot see that, because a
                # number the shape rule refuses has no key to compare: a
                # contact whose number somebody once typed as `N/A` would
                # otherwise be locked out of the POS customer form for good -
                # that form re-sends `phone` whatever was edited, so every
                # attempt to fix the NAME would be refused over the number,
                # and the cashier would be stuck unless they happened to have
                # the customer's real number to hand. The rule is about a
                # number being entered; re-sending an untouched one is not
                # entering it.
                if vals['phone'] == (partner.phone or ''):
                    continue
                if not vals.get('parent_id', partner.parent_id):
                    self._laundry_check_phone(
                        vals['phone'], exclude_id=partner.id
                    )
        return super().write(vals)
