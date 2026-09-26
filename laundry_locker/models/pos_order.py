import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# An order only takes a drop-off once it has been rung up. Everything before
# that is a basket, and a basket reserves nothing.
VALIDATED_STATES = ('paid', 'done', 'invoiced')


class PosOrder(models.Model):
    _inherit = 'pos.order'

    # Set at the till when a locker transaction is picked, and synced with the
    # order like the other laundry_* fields (pos.order has no
    # _load_pos_data_fields override, so it loads and syncs automatically).
    laundry_locker_ref = fields.Char(
        string='Locker Ref #', copy=False, index=True,
        help="PudoPro reference of the locker drop-off this order bills.",
    )
    # Copied onto the order rather than read back off the transaction: the
    # receipt is reprinted from order history, on tills that never loaded the
    # locker queue, and long after the door has been let to someone else.
    laundry_locker_door = fields.Char(
        string='Locker Door', copy=False,
        help="Door the laundry was dropped into, as it was at drop-off.",
    )

    def _sync_laundry_locker(self):
        """Settle locker drop-offs against the orders that actually took them.

        Picking a drop-off at the till reserves NOTHING: an order still being
        built has taken nothing, so the drop-off stays unbilled and stays in
        every till's picker, and an abandoned order needs no cleanup. It is
        billed here instead, when an order is validated - and whichever order
        is validated first is the one that gets it.

        The same hook hands one back: a refund means the bag is going home
        unwashed, so the drop-off returns to the queue to be billed again.
        """
        Transaction = self.env['laundry.locker.transaction']
        for order in self:
            if order.state not in VALIDATED_STATES:
                continue

            if order.laundry_locker_ref:
                transaction = Transaction.search(
                    [('ref', '=', order.laundry_locker_ref)], limit=1
                )
                if transaction:
                    transaction._laundry_take(order)

            # Read off the REFUNDED lines, not off this order's own ref: a
            # refund is built by the till from the original's lines and does
            # not carry the locker ref across.
            originals = order.lines.refunded_orderline_id.order_id
            for original in originals.filtered('laundry_locker_ref'):
                transaction = Transaction.search(
                    [('ref', '=', original.laundry_locker_ref)], limit=1
                )
                # Only give back what that order actually took - if another
                # order won the drop-off, refunding this one must not free it.
                if transaction and transaction.pos_order_id == original:
                    transaction._laundry_give_back()

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._sync_laundry_locker()
        return orders

    def write(self, vals):
        result = super().write(vals)
        # `state` matters as much as the ref: an order that syncs as a draft
        # and is validated later reaches its billing moment through a state
        # change, with the ref untouched.
        if 'laundry_locker_ref' in vals or 'state' in vals:
            self._sync_laundry_locker()
        return result
