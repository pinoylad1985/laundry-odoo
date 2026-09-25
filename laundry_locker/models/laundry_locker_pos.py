import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .laundry_locker_transaction import phone_last10

_logger = logging.getLogger(__name__)


class LaundryLockerTransaction(models.Model):
    _inherit = 'laundry.locker.transaction'

    # ------------------------------------------------------------------
    # what the POS picker reads
    # ------------------------------------------------------------------
    def _pos_datetime(self, value):
        """A datetime as the till should read it: local time, 12-hour.

        Datetimes are stored UTC-naive, and the POS shows PH time - printing
        the stored value would be 8 hours out.
        """
        if not value:
            return ''
        local = fields.Datetime.context_timestamp(self, value)
        hour = local.hour % 12 or 12
        meridiem = 'AM' if local.hour < 12 else 'PM'
        return f'{local:%Y-%m-%d} {hour}:{local:%M} {meridiem}'

    def _pos_schedule(self, value):
        """A stored datetime as the New Order modal's own date + hour pair.

        The modal keeps its schedule as a `YYYY-MM-DD` string and an `HH:00`
        hour key, so handing it those directly is what lets a locker booking
        drop into the schedule step without being re-picked.
        """
        if not value:
            return {'date': '', 'hour': ''}
        local = fields.Datetime.context_timestamp(self, value)
        return {'date': f'{local:%Y-%m-%d}', 'hour': f'{local:%H}:00'}

    def _pos_row(self):
        """One transaction as the till's picker shows it."""
        self.ensure_one()
        return {
            'id': self.id,
            'ref': self.ref or '',
            'location_name': self.location_name or '',
            'customer_name': self.customer_name or '',
            'phone': self.phone or '',
            'service': self.service or '',
            'turnaround': self.turnaround or '',
            'dirty_door': self.dirty_door or '',
            'status_label': self.status_label or '',
            'customer_match': self.customer_match or 'new',
            'partner_id': self.partner_id.id or False,
            'partner_name': self.partner_id.name or '',
            'phone_verified': self.phone_verified,
            'new_laundry_at': self._pos_datetime(self.new_laundry_at),
            'created_at': self._pos_datetime(self.created_at),
            'pickup_datetime': self._pos_datetime(self.pickup_datetime),
            'delivery_datetime': self._pos_datetime(self.delivery_datetime),
            # The same two dates again, in the shape the New Order modal fills
            # its schedule step from - the strings above are for reading.
            'schedule': {
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
        transactions = self.search([('billed', '=', False)], limit=limit)
        return [tx._pos_row() for tx in transactions]

    @api.model
    def get_rows_for_pos(self, transaction_ids):
        """Specific rows, billed or not - for re-opening one already taken."""
        return [tx._pos_row() for tx in self.browse(transaction_ids).exists()]

    @api.model
    def pos_check_phone(self, phone):
        """Re-run the Returning/New lookup for a number typed at the till."""
        key = phone_last10(phone)
        partner = self.env['res.partner']
        if key:
            partner = partner.search(
                [('laundry_phone_last10', '=', key)], limit=1, order='id'
            )
        return {
            'last10': key,
            'customer_match': 'returning' if partner else 'new',
            'partner_id': partner.id or False,
            'partner_name': partner.name or '',
        }

    # ------------------------------------------------------------------
    # taking one at the till
    # ------------------------------------------------------------------
    def pos_claim(self, phone=None, customer_name=None, phone_verified=False):
        """Put this transaction on the order being built at the till.

        This RESERVES NOTHING. A drop-off stays unbilled and stays in every
        till's picker until an order is actually validated, so the same bag can
        be picked at two tills and the sale that is rung up first is the one
        that takes it (see pos.order._sync_laundry_locker). The cost of that is
        one till occasionally losing a basket it was building; the cost of
        reserving it at the pick was worse - an order abandoned mid-build
        stranded the drop-off, billed against nothing and gone from the queue.

        What this DOES settle is the customer: a number that matched nobody
        becomes a contact here, on the cashier's word that it rang.
        """
        self.ensure_one()
        # The only claim still refused: a validated order already took it, and
        # that is not something a second till can undo from the picker.
        if self.billed:
            order_ref = self.pos_order_id.pos_reference
            if order_ref:
                raise UserError(
                    _('Ref %(ref)s has already been billed on %(order)s.',
                      ref=self.ref, order=order_ref)
                )
            raise UserError(_('Ref %s has already been billed.') % self.ref)

        corrections = {}
        if phone is not None and (phone or '').strip() != (self.phone or ''):
            corrections['phone'] = (phone or '').strip() or False
        if customer_name is not None and (customer_name or '').strip() != (self.customer_name or ''):
            corrections['customer_name'] = (customer_name or '').strip() or False
        if phone_verified:
            corrections['phone_verified'] = True
        if corrections:
            # Writing the phone re-runs the match, so partner_id below is
            # already the answer for the CORRECTED number.
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
            if not self.phone:
                raise UserError(_('A phone number is needed to create the customer.'))
            partner = self.env['res.partner'].create({
                'name': self.customer_name or self.ref,
                'phone': self.phone,
            })
            self.partner_id = partner

        # The same shape a picker row has, so the modal reads one thing whether
        # it came from the list or from a claim. The partner is pinned on top
        # because it may have been created a few lines above.
        result = self._pos_row()
        result.update({'partner_id': partner.id, 'partner_name': partner.name})
        return result

    def _mark_billed(self, pos_order=None):
        vals = {'billed': True, 'billed_date': fields.Datetime.now()}
        if pos_order:
            vals['pos_order_id'] = pos_order.id
        self.write(vals)
        self._push_billed_to_firebase()

    def action_unbill(self):
        """Put a transaction back in the picker - a rescue, run by hand."""
        self.write({'billed': False, 'billed_date': False, 'pos_order_id': False})
        self._push_billed_to_firebase()

    # ------------------------------------------------------------------
    # billing, which happens when an order is validated - not when it is built
    # ------------------------------------------------------------------
    def _laundry_take(self, pos_order):
        """Bill this drop-off on a validated order. First one there wins.

        Two tills can both have this drop-off on an order they are building;
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
            transaction.action_unbill()
