from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    # Set at the till when a locker transaction is picked, and synced with the
    # order like the other laundry_* fields (pos.order has no
    # _load_pos_data_fields override, so it loads and syncs automatically).
    laundry_locker_ref = fields.Char(
        string='Locker Ref #', copy=False, index=True,
        help="PudoPro reference of the locker drop-off this order bills.",
    )

    def _link_laundry_locker(self):
        """Point the locker transaction at the order that billed it.

        The transaction was already marked billed at the till (so a second till
        could not take it while this order was being built); this is where it
        learns WHICH order, once that order exists server-side.
        """
        Transaction = self.env['laundry.locker.transaction']
        for order in self.filtered('laundry_locker_ref'):
            transaction = Transaction.search(
                [('ref', '=', order.laundry_locker_ref)], limit=1
            )
            if not transaction or transaction.pos_order_id == order:
                continue
            transaction.pos_order_id = order
            if not transaction.billed:
                transaction._mark_billed(order)
            else:
                # Already billed - just let the dashboard know the order number.
                transaction._push_billed_to_firebase()

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._link_laundry_locker()
        return orders

    def write(self, vals):
        result = super().write(vals)
        if 'laundry_locker_ref' in vals:
            self._link_laundry_locker()
        return result
