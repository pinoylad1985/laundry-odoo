from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Backfill account.move.line.pos_order_id on databases that already had 1.1.0.

    The field shipped in 1.1.0 but only got filled for sessions closed after the
    install, so every historical receivable line still shows a blank order in the
    detailed report. Fresh installs go through post_init_hook in __init__.py.
    """
    # Odoo has passed a cursor here for many releases; tolerate an Environment
    # in case that changes, rather than failing mid-upgrade.
    env = cr if isinstance(cr, api.Environment) else api.Environment(cr, SUPERUSER_ID, {})
    env['pos.session']._laundry_backfill_pos_order_links()
