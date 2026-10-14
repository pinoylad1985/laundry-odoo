# Multiple services per order are now tracked separately: each service carries its own
# staff, processed date and status on laundry.order.service, and an order counts as
# finished only once EVERY service is Done (pos_order.laundry_all_done).
#
# This fills the new rows in from what each order already recorded, and repoints the
# PENDINGS filter at the roll-up.
import logging

from odoo.addons.laundry_pos import _laundry_backfill_services

_logger = logging.getLogger(__name__)

# Same three conditions as before, with the first one swapped: "pending" used to mean the
# order's single status was open, it now means not every service is Done.
PENDING_DOMAIN = (
    "[('laundry_all_done', '=', False), "
    "('laundry_service_type', 'in', ['dropoff', 'dropoff_delivery', 'pickup_delivery', 'locker']), "
    "('laundry_secondary_type', 'in', ['order'])]"
)


def migrate(cr, version):
    if not version:
        return

    # One tracking row per service on every existing order, carrying that order's single
    # staff / processed date / status onto each of them. Shared with the post-init hook so
    # an upgrade and an install-onto-existing-data produce the same rows.
    _laundry_backfill_services(cr)

    # The PENDINGS filter lives in data/pos_order_filters.xml under noupdate="1" (so the
    # owner's sort/column tweaks survive upgrades), which also means its domain is NOT
    # reloaded from the XML. Left alone it would keep filtering on laundry_status and the
    # POS dashboard's Orders link would stop agreeing with the Orders Pending menu.
    # Matched through ir_model_data rather than by name, so a renamed filter still lands.
    cr.execute(
        """
        UPDATE ir_filters f
           SET domain = %s
          FROM ir_model_data d
         WHERE d.module = 'laundry_pos'
           AND d.name = 'pos_order_filter_pendings'
           AND d.model = 'ir.filters'
           AND d.res_id = f.id
        """,
        (PENDING_DOMAIN,),
    )
    _logger.info("laundry_pos 1.5.0: repointed %s PENDINGS filter(s) at laundry_all_done", cr.rowcount)
