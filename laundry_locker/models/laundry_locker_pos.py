import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .laundry_locker_sync import CLEARED_STATUS
from .laundry_locker_transaction import (
    corrected_name, corrected_phone, ph_local_phone, phone_last10,
)
from .pos_order import VALIDATED_STATES

_logger = logging.getLogger(__name__)

# The second address line every locker customer gets, under the site name.
LOCKER_ADDRESS_LINE2 = 'LOCKER'


class LaundryLockerTransaction(models.Model):
    _inherit = 'laundry.locker.transaction'

    # ------------------------------------------------------------------
    # what the POS picker reads
    # ------------------------------------------------------------------
    def _pos_datetime(self, value):
        """A datetime as the register should read it: local time, 12-hour.

        Datetimes are stored UTC-naive, and the POS shows PH time - printing
        the stored value would be 8 hours out.
        """
        if not value:
            return ''
        local = fields.Datetime.context_timestamp(self, value)
        hour = local.hour % 12 or 12
        meridiem = 'AM' if local.hour < 12 else 'PM'
        return f'{local:%Y-%m-%d} {hour}:{local:%M} {meridiem}'

    def _pos_schedule(self, value, exact=False):
        """A stored datetime as the New Order modal's own date + hour pair.

        The modal keeps its schedule as a `YYYY-MM-DD` string and an `HH:00`
        hour key, so handing it those directly is what lets a locker booking
        drop into the schedule step without being re-picked.

        A slot CHOSEN at the locker is one of those keys and its minutes are
        always 00. `exact` is for the one datetime that is not a chosen slot -
        the moment the bag became laundry, which carries real minutes and is
        NOT rounded to an hour, because a turnaround measured from it would
        then be off by as much as an hour. The minutes ride along in the same
        `hour` string as `HH:MM`, which the modal parses without any special
        case (it builds `<date>T<hour>:00`).
        """
        if not value:
            return {'date': '', 'hour': ''}
        local = fields.Datetime.context_timestamp(self, value)
        minutes = f'{local:%M}' if exact else '00'
        return {'date': f'{local:%Y-%m-%d}', 'hour': f'{local:%H}:{minutes}'}

    def _pos_row(self):
        """One transaction as the register's picker shows it."""
        self.ensure_one()
        return {
            'id': self.id,
            'ref': self.ref or '',
            'location_name': self.location_name or '',
            # FOUR keys, named exactly as the four fields behind them, so
            # the register and the Lockers list cannot call one fact two
            # things. Each is a PAIR: what the customer keyed in at the
            # locker - which is what is printed on their drop-off and what
            # they quote when they ring up about it - and the shop's
            # correction, BESIDE it and never over it.
            #
            # A correction is blank unless something was really changed (the
            # server blanks a no-op on write), which is what lets the picker
            # print an em-dash there: repeating the number in both columns
            # would read as a correction that changed nothing. The value in
            # force is the correction or, failing that, the locker's own -
            # utils/locker_rows.js on the register, _laundry_name_in_force /
            # _laundry_phone_in_force here.
            #
            # Both numbers are normalised on the way out as well as on the way
            # in, so a row that predates the trunk-0 rule still reads right.
            'customer_name': self.customer_name or '',
            'customer_name_corrected': self.customer_name_corrected or '',
            'phone': ph_local_phone(self.phone) or '',
            'phone_corrected': ph_local_phone(self.phone_corrected) or '',
            'service': self.service or '',
            'turnaround': self.turnaround or '',
            'dirty_door': self.dirty_door or '',
            'status_label': self.status_label or '',
            'customer_match': self.customer_match or 'new',
            'partner_id': self.partner_id.id or False,
            'partner_name': self.partner_id.name or '',
            # The matched customer as labelled facts - name, phone, the two
            # address lines, tags. The picker printed the name alone until
            # v1.1.17, which does not say WHICH customer the number landed
            # on: the book holds several people called MARY, and a bag put on
            # the wrong one is not noticed again until somebody comes to
            # claim it. Built by laundry_pos so the register, the contact form
            # and the Orders list cannot describe one customer three ways.
            # The tag is in there because it is the one thing a Locker order
            # asks of a contact that this screen can still do something
            # about - read here rather than found out when the modal refuses
            # to continue.
            'partner_parts': (
                self.partner_id._laundry_contact_parts()
                if self.partner_id else {}
            ),
            # Whether that contact has been taken OUT of the book. Carried so
            # the picker can say so and offer to put it back, rather than let
            # the cashier work the whole drop-off and be refused at Confirm:
            # an archived contact is invisible to the register's customer
            # list, but the one-customer-per-number rule counts it, so its
            # number can never become a new contact either.
            'partner_archived': (
                bool(self.partner_id) and not self.partner_id.active
            ),
            # Why this row's number cannot become a contact, if it cannot -
            # the feed sends whatever was keyed in at the locker, and a number
            # with letters in it or missing digits arrives here like any other.
            # Carried on the row so the picker can open the correction fields
            # the moment the row is chosen, instead of letting the cashier
            # call an unusable number, answer that it rang, and be refused by
            # res.partner.create at Confirm. Empty on a matched row, where the
            # contact already exists and nothing is about to be created.
            'phone_problem': (
                '' if self.partner_id
                else self.env['res.partner'].laundry_phone_problem_message(
                    self._laundry_phone_in_force()
                )
            ),
            'phone_verified': self.phone_verified,
            'new_laundry_at': self._pos_datetime(self.new_laundry_at),
            'created_at': self._pos_datetime(self.created_at),
            'pickup_datetime': self._pos_datetime(self.pickup_datetime),
            'delivery_datetime': self._pos_datetime(self.delivery_datetime),
            # The same dates again, in the shape the New Order modal fills its
            # schedule step from - the strings above are for reading. The
            # deposit is not a slot anyone picked and the modal never fills a
            # picker from it; it is here because a Locker order's turnaround is
            # measured FROM it (see the modal's own diffHours), which needs it
            # in the same comparable shape as the two slots.
            'schedule': {
                'deposit': self._pos_schedule(self.new_laundry_at, exact=True),
                'pickup': self._pos_schedule(self.pickup_datetime),
                'delivery': self._pos_schedule(self.delivery_datetime),
            },
        }

    @api.model
    def get_unbilled_for_pos(self, limit=300):
        """The unbilled locker drop-offs, newest first.

        Fetched when LOCKER is picked rather than pre-loaded with the session:
        a drop-off that happens mid-shift has to be pickable without reopening
        the register.
        """
        # Unbilled AND not Cleared. A clear is PudoPro saying the drop-off is
        # no longer owed for, so it is as unbillable as one already sold - and
        # the cashier never sees a row they would have to know not to take.
        #
        # Read straight off the feed's own status on every pull, so it needs no
        # undoing: a drop-off un-cleared at the locker end comes back here by
        # itself. See CLEARED_STATUS in laundry_locker_sync for why the match is
        # exact. Odoo's `!=` covers a NULL status too, so a row the feed sent
        # without one still queues rather than vanishing.
        transactions = self.search(
            [('billed', '=', False),
             ('status_label', '!=', CLEARED_STATUS)], limit=limit
        )
        return [tx._pos_row() for tx in transactions]

    @api.model
    def get_rows_for_pos(self, transaction_ids):
        """Specific rows, billed or not - for re-opening one already taken."""
        return [tx._pos_row() for tx in self.browse(transaction_ids).exists()]

    @api.model
    def get_row_for_pos_ref(self, ref):
        """The row behind a ref an order is already carrying.

        For a Locker order being edited or reopened after a reload: the order
        remembers the ref and nothing else about the drop-off. Reading it back
        through _pos_row - rather than a field list of its own - is what keeps
        the modal reading ONE shape whether the drop-off arrived from the queue,
        from a claim, or from an order it was already on. A field the modal
        needs then cannot be present on one path and missing on another, which
        is how the turnaround came to be measured differently on a reopened
        order than on a fresh one.
        """
        # active_test=False: the order is ASKING for a ref it already holds,
        # so this is a read-back of a known row and not a queue to be filtered.
        # An archived one - deleted or cleared at the locker end - would
        # otherwise come back False and strip the locker details off an order
        # already taking it, on nothing more than a page reload. The queue that
        # does need filtering is get_unbilled_for_pos, and it still is.
        transaction = self.with_context(active_test=False).search(
            [('ref', '=', ref)], limit=1
        )
        return transaction._pos_row() if transaction else False

    @api.model
    def pos_check_phone(self, phone):
        """Re-run the Returning/New lookup for a number typed at the register.

        A number the shop's own rules refuse - letters in it, too few digits,
        a filler - is answered with WHY and no lookup at all. Looking it up
        anyway would report "New", which is true and leads straight into a
        trap: the cashier would call it, answer that it rang, press Confirm,
        and only then be refused by res.partner.create - having by then lost
        the one thing Confirm was going to do. The message is the server's own
        (res.partner.laundry_phone_problem_message), so the counter and the
        contact form state the rule in the same words.
        """
        message = self.env['res.partner'].laundry_phone_problem_message(phone)
        if message:
            return {
                'problem': message,
                'last10': '',
                # Not 'new'. No match either way, which is also what keeps
                # Confirm disabled while the number is unusable.
                'customer_match': False,
                'partner_id': False,
                'partner_name': '',
                'partner_parts': {},
                'partner_archived': False,
            }
        key = phone_last10(phone)
        partner = self.env['res.partner']
        if key:
            # ARCHIVED contacts count here too - the same reasoning as
            # _compute_partner_id. A lookup that could not see one would
            # answer "New" about a number that can never become a new
            # contact, and the cashier would find that out at Confirm.
            partner = partner.with_context(active_test=False).search(
                [('laundry_phone_last10', '=', key)], limit=1,
                order='active desc, id',
            )
        return {
            'last10': key,
            'customer_match': 'returning' if partner else 'new',
            'partner_id': partner.id or False,
            'partner_name': partner.name or '',
            # The same parts as the row above, for the same reason: the
            # cashier is about to hand a bag to whoever this is - the tag
            # included, since Locker is the service type that requires one.
            'partner_parts': (
                partner._laundry_contact_parts() if partner else {}
            ),
            'partner_archived': bool(partner) and not partner.active,
        }

    # ------------------------------------------------------------------
    # taking one at the register
    # ------------------------------------------------------------------
    def _laundry_own_contact(self):
        """The contact this drop-off created, if it is still ours to correct.

        A contact made from a locker number exists on one cashier's word that
        the number rang. When that word was wrong, the correction has to reach
        back to the record it caused - otherwise the first contact stays in the
        book holding a number nobody answers, and under the
        one-customer-per-number rule it SQUATS on it: the real owner of that
        number can never be added, and the only sign anything is wrong is two
        contacts with the same name.

        "Ours" is narrow on purpose. The moment anything else has used the
        contact it has stopped being this drop-off's mistake and become
        somebody's customer record, and editing it from here would be a
        different and worse bug than the one being fixed. So: created here,
        still the match, and used by nothing else.
        """
        self.ensure_one()
        partner = self.created_partner_id
        if not partner or partner != self.partner_id:
            return self.env['res.partner'].browse()
        # Another drop-off has since matched this contact - it is a returning
        # customer now, whatever it started as.
        others = self.search_count([
            ('partner_id', '=', partner.id), ('id', '!=', self.id),
        ])
        if others:
            return self.env['res.partner'].browse()
        # Sales, invoices or sub-contacts: all of them make it somebody's
        # record.
        #
        # VALIDATED sales only. The order this drop-off is being built onto may
        # already have been pushed to the server as a draft, carrying this very
        # contact - counting that would block the correction in exactly the
        # case it exists for. A draft is an order still being built, and
        # correcting its customer is what the cashier is in the middle of
        # doing; a validated sale is the point where the contact stops being
        # this drop-off's mistake.
        if self.env['pos.order'].sudo().search_count([
            ('partner_id', '=', partner.id), ('state', 'in', VALIDATED_STATES),
        ]):
            return self.env['res.partner'].browse()
        if self.env['account.move'].sudo().search_count([('partner_id', '=', partner.id)]):
            return self.env['res.partner'].browse()
        if partner.child_ids or partner.parent_id:
            return self.env['res.partner'].browse()
        return partner

    def _laundry_absorb_correction(self, new_phone, verified=False):
        """Carry a number correction onto the contact this drop-off created.

        Runs BEFORE the transaction's own phone is written, which is what makes
        it tidy rather than clever: once the contact already holds the corrected
        number, the match recomputes onto that same contact by itself and
        nothing downstream has to know a correction happened.

        Two endings, and both leave exactly one contact:
          - the corrected number belongs to NOBODY - the contact we made is
            simply re-numbered, keeping the name, the locker address and the id;
          - the corrected number belongs to a contact already in the book (the
            cashier mistyped a returning customer) - ours is deleted, because
            there is nothing left for it to be. Archiving would not do: an
            archived contact still holds its number against the duplicate rule,
            so the wrong number would go on squatting, invisibly.
        """
        self.ensure_one()
        own = self._laundry_own_contact()
        if not own:
            return
        key = phone_last10(new_phone)
        if not key:
            return
        twin = self.env['res.partner'].with_context(active_test=False).search(
            [('laundry_phone_last10', '=', key), ('id', '!=', own.id),
             ('parent_id', '=', False)],
            limit=1, order='id',
        )
        if not twin:
            if not verified:
                # Re-numbering our contact onto a number nobody has called
                # would rebuild the very thing the gate exists to stop: a
                # contact standing on a number that rings nowhere. Left alone,
                # the match below finds nothing and the claim is refused with
                # "call it first", which is the right answer. The twin branch
                # needs no call - matching a customer already in the book is
                # not a claim about the number.
                return
            own.phone = ph_local_phone(new_phone)
            return
        self.created_partner_id = False
        self.partner_id = twin
        # The draft the register pushed still points at it (see above), and a
        # delete would tear the customer off an order that is on screen. The
        # register re-points that order at the twin a moment from now, but not
        # from in here, so the stray contact is left for the cleanup it is.
        if self.env['pos.order'].sudo().search_count([('partner_id', '=', own.id)]):
            return
        try:
            own.unlink()
        except Exception:
            # Something references it after all. Leaving it is the safe
            # ending - the correction itself has already landed on the
            # transaction, and a stray contact is a cleanup job, not a
            # wrong order.
            _logger.warning(
                'Locker %s: could not remove the contact it created (%s).',
                self.ref, own.display_name,
            )

    @api.model
    def pos_customer_tags(self):
        """The tags the picker offers on a contact it is about to create.

        Fetched rather than read off the session: res.partner.category is NOT
        loaded into the POS - res.partner.pos_tag_names exists precisely
        because the relation is not there to read.

        `name`, not `display_name`, so a tag reads here exactly as it reads on
        the receipt (pos_tag_names joins the same field).
        """
        return self.env['res.partner.category'].search_read(
            [], ['name'], order='name',
        )

    def pos_claim(self, phone=None, customer_name=None, phone_verified=False,
                  tag_ids=None, restore_partner=False):
        """Put this transaction on the order being built at the register.

        This RESERVES NOTHING. A drop-off stays unbilled and stays in every
        register's picker until an order is actually validated, so the same bag can
        be picked at two registers and the sale that is rung up first is the one
        that takes it (see pos.order._sync_laundry_locker). The cost of that is
        one register occasionally losing a basket it was building; the cost of
        reserving it at the pick was worse - an order abandoned mid-build
        stranded the drop-off, billed against nothing and gone from the queue.

        What this DOES settle is the customer: a number that matched nobody
        becomes a contact here, on the cashier's word that it rang.

        `tag_ids` is used ONLY on a contact this creates. A Locker order needs
        a tag on its contact and a drop-off cannot supply one, so the picker
        asks for it at the one moment the contact is being made. A RETURNING
        customer's own tags are left alone - they are the book's answer, not
        this screen's.

        `restore_partner` is the cashier's answer about an ARCHIVED match. The
        picker names its button after what it does ("Restore and use"), the
        same way the contact form's duplicate dialog does, and nothing goes
        back into the book without it.
        """
        self.ensure_one()
        # The only claim still refused: a validated order already took it, and
        # that is not something a second register can undo from the picker.
        if self.billed:
            order_ref = self.pos_order_id.pos_reference
            if order_ref:
                raise UserError(
                    _('Ref %(ref)s has already been billed on %(order)s.',
                      ref=self.ref, order=order_ref)
                )
            raise UserError(_('Ref %s has already been billed.') % self.ref)

        corrections = {}
        # The register sends back whatever is in its two boxes, touched or
        # not, so each is turned into a CORRECTION of the locker's own value -
        # and False where it corrects nothing, which is also how a cashier
        # retyping the original clears a correction made earlier.
        #
        # A number typed at the register gets the same trunk 0 the feed's
        # does (corrected_phone normalises it), so a correction cannot be the
        # same number spelled without it.
        if phone is not None:
            new_phone = corrected_phone(phone, self.phone)
            if (new_phone or '') != (self.phone_corrected or ''):
                corrections['phone_corrected'] = new_phone
        if customer_name is not None:
            new_name = corrected_name(customer_name, self.customer_name)
            if (new_name or '') != (self.customer_name_corrected or ''):
                corrections['customer_name_corrected'] = new_name
        if phone_verified:
            corrections['phone_verified'] = True
        if 'phone_corrected' in corrections:
            # Before the write, not after: see _laundry_absorb_correction. It
            # is handed the number about to be IN FORCE - the correction, or
            # the locker's own where a correction is being cleared.
            self._laundry_absorb_correction(
                corrections['phone_corrected'] or self.phone,
                verified=bool(phone_verified),
            )
        if corrections:
            # Writing the correction re-runs the match - phone_last10 is keyed
            # on the number in force - so partner_id below is already the
            # answer for the CORRECTED number.
            self.write(corrections)

        partner = self.partner_id
        if not partner:
            if not self.phone_verified:
                raise UserError(_(
                    'This number has no customer yet. Call it and confirm it rings '
                    'before a contact is created from it - a locker customer who '
                    'mistyped their number would otherwise be saved under it '
                    'permanently.'
                ))
            # The name and number IN FORCE: a contact is created from what
            # the register corrected them to, not from what the locker sent.
            in_force_phone = self._laundry_phone_in_force()
            if not in_force_phone:
                raise UserError(_('A phone number is needed to create the customer.'))
            partner_vals = {
                'name': self._laundry_name_in_force() or self.ref,
                'phone': in_force_phone,
            }
            if tag_ids:
                partner_vals['category_id'] = [(6, 0, list(tag_ids))]
            partner = self.env['res.partner'].create(partner_vals)
            self.partner_id = partner
            # Recorded so a later correction can reach back to THIS record
            # rather than leaving it behind - see _laundry_own_contact.
            self.created_partner_id = partner

        if not partner.active:
            # The number belongs to a contact somebody took out of the book.
            # It is OFFERED rather than refused, and restored rather than
            # worked around, for the same reason the contact form offers it
            # (laundry_pos.laundry_restore_phone_twin): the number cannot
            # become a second contact, so refusing here would leave the
            # cashier with a customer in front of them, a bag in hand and
            # nowhere to go - and the way out of a dead end is the duplicate
            # this whole rule exists to prevent.
            #
            # Restored BEFORE it is used. An archived contact is invisible to
            # get_new_partner, to the customer list and to an ordinary read,
            # so the order would quietly end up with no customer on it.
            if not restore_partner:
                raise UserError(_(
                    'That number belongs to %s, whose contact is archived. '
                    'Press "Restore and use" to put it back in the book.'
                ) % partner.sudo().name)
            self.env['res.partner'].laundry_restore_phone_twin(partner.id)

        self._apply_locker_address(partner)
        # The same shape a picker row has, so the modal reads one thing whether
        # it came from the list or from a claim. The partner is pinned on top
        # because it may have been created a few lines above.
        result = self._pos_row()
        result.update({'partner_id': partner.id, 'partner_name': partner.name})
        return result

    def _apply_locker_address(self, partner):
        """Give a locker customer the locker as their address.

        The site on one line and LOCKER on the next, so the receipt says where
        the bag came from and a rider reading it knows it is not a house call.

        Only onto an EMPTY address block. A locker customer who has never given
        one has nothing to lose; a customer who already has an address is
        someone the shop knows by it, and a drop-off is no reason to overwrite
        where they live.
        """
        self.ensure_one()
        if not partner or partner.street or partner.street2:
            return
        if not self.location_name:
            return
        partner.write({'street': self.location_name, 'street2': LOCKER_ADDRESS_LINE2})

    def _mark_billed(self, pos_order=None, push=True):
        vals = {'billed': True, 'billed_date': fields.Datetime.now()}
        if pos_order:
            vals['pos_order_id'] = pos_order.id
        self.write(vals)
        # `push` is off only where the dashboard was already told - see
        # _laundry_adopt_billed_orders.
        if push:
            self._push_billed_to_firebase()

    def _laundry_adopt_billed_orders(self):
        """Work out, from scratch, which of these drop-offs are already sold.

        The locker list is disposable: it is rebuilt from the feed, and rows
        are deleted and resynced whenever the mapping changes. What is NOT
        disposable is the order - `pos.order.laundry_locker_ref` is written at
        the register, stored, and stays there for the life of the sale. So a row
        arriving from the feed asks the orders about itself, instead of
        reappearing in every register's picker for a bag that was collected weeks
        ago.

        An order is the best answer but not the only one, because not every
        locker drop-off was ever sold through Odoo. Everything from before
        this shop billed lockers here has no order to be found, and marking
        those billed by hand lasted only until the next rebuild put them all
        back in the queue. So two standing statements are consulted after the
        orders, both re-applied on every sync:

          * the cutoff date - `laundry_locker.billed_before` - under which a
            drop-off predates Odoo and was therefore settled some other way;
          * `laundry.locker.billing.override`, the per-ref exceptions in both
            directions (Lockers > Billing Overrides).

        Order of precedence is the order of confidence. A real paid order
        outranks anything anyone typed; a typed decision about one ref
        outranks a date rule covering thousands.

        Runs over every row a sync touches, not only the new ones. Nothing
        unbills a drop-off by hand any more - a wrongly-billed one is refunded,
        and a refunded sale is skipped below - so an unbilled row that a paid
        order is holding is not somebody's decision, it is drift, and drift
        should heal itself. For the same reason this only ever BILLS: a `hold`
        override does not unbill a row, it declines to bill one.
        """
        candidates = self.filtered(lambda t: t.ref and not t.billed)
        if not candidates:
            return
        orders = self.env['pos.order'].search(
            [('laundry_locker_ref', 'in', candidates.mapped('ref')),
             ('state', 'in', VALIDATED_STATES)],
            order='id',
        )
        by_ref = {}
        for order in orders:
            # A refunded sale handed the drop-off back, so it is unbilled and
            # has to stay that way - see _laundry_give_back. Refunds here are
            # always whole-order, so one refunded line settles it.
            if order.lines.refund_orderline_ids:
                continue
            by_ref.setdefault(order.laundry_locker_ref, order)  # first one wins
        overrides = self.env['laundry.locker.billing.override']
        rules = overrides._laundry_rules()
        cutoff = overrides._laundry_billed_before()
        predating = 0

        for transaction in candidates:
            order = by_ref.get(transaction.ref)
            if order:
                # Nothing is pushed to Firebase: /lockerBilled already says
                # billed from the original sale, and the feed never rebuilds
                # that node. Re-announcing it on a full resync would be a few
                # hundred writes saying what is already there.
                transaction._mark_billed(order, push=False)
                _logger.info(
                    'Locker %s re-adopted by its existing order %s.',
                    transaction.ref, order.pos_reference,
                )
                continue

            rule = rules.get((transaction.ref or '').strip().lower())
            if rule == 'hold':
                # Named as still to be sold. Nothing below may bill it.
                continue
            if rule == 'billed':
                # Somebody stated this one specifically - a sale Odoo has no
                # record of. That IS news to the dashboard, unlike the two
                # cases either side of it, so it is pushed. The push only
                # warns if it is refused; billing does not depend on it.
                transaction._mark_billed(push=True)
                continue
            if cutoff and transaction.new_laundry_at and transaction.new_laundry_at < cutoff:
                # Predates Odoo billing lockers at all. No order to name, and
                # no push: this is hundreds of rows on a full resync, and the
                # dashboard draws the same line for itself rather than being
                # told about each one.
                transaction._mark_billed(push=False)
                predating += 1

        if predating:
            # Counted rather than logged one by one: on a full resync this is
            # every drop-off the shop ever took before Odoo was billing them,
            # and a line each would bury everything else in the log.
            _logger.info(
                '%d locker drop-offs billed as predating %s.', predating, cutoff,
            )

    def _laundry_unbill(self):
        """Put a transaction back in the picker.

        Internal, not a button: the only thing that unbills a drop-off is a
        refund of the sale that took it - see _laundry_give_back.
        """
        self.write({'billed': False, 'billed_date': False, 'pos_order_id': False})
        self._push_billed_to_firebase()

    # ------------------------------------------------------------------
    # billing, which happens when an order is validated - not when it is built
    # ------------------------------------------------------------------
    def _laundry_take(self, pos_order):
        """Bill this drop-off on a validated order. First one there wins.

        Two registers can both have this drop-off on an order they are building;
        only one of them can have sold it. Returns whether `pos_order` is the
        one that got it.
        """
        self.ensure_one()
        if self.billed and self.pos_order_id and self.pos_order_id != pos_order:
            _logger.info(
                'Locker %s was already billed on %s; %s did not take it.',
                self.ref, self.pos_order_id.pos_reference, pos_order.pos_reference,
            )
            return False
        if not self.billed:
            self._mark_billed(pos_order)
            return True
        # Billed with no order on it - marked by hand - so this sale adopts it.
        if self.pos_order_id != pos_order:
            self.pos_order_id = pos_order
        self._push_billed_to_firebase()
        return True

    def _laundry_give_back(self):
        """Hand a billed drop-off back - its sale was refunded.

        The bag is going home unwashed, so the drop-off returns to the queue
        and can be billed again, by a rebooking most often.
        """
        for transaction in self.filtered('billed'):
            transaction._laundry_unbill()
