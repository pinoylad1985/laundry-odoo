# Rider fields split: the single `laundry_rider` (the PICKUP rider, captured at the
# order sign-off) is renamed to `laundry_pickup_rider`, and a new `laundry_delivery_rider`
# is added for the delivery sign-off (process to follow). Rename the column here — BEFORE
# the ORM loads the new field — so existing pickup-rider values on past orders are kept.
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'pos_order' AND column_name = 'laundry_rider'"
    )
    has_old = bool(cr.fetchone())
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'pos_order' AND column_name = 'laundry_pickup_rider'"
    )
    has_new = bool(cr.fetchone())
    if has_old and not has_new:
        cr.execute("ALTER TABLE pos_order RENAME COLUMN laundry_rider TO laundry_pickup_rider")
        _logger.info("laundry_pos 1.4.5: renamed pos_order.laundry_rider -> laundry_pickup_rider")
