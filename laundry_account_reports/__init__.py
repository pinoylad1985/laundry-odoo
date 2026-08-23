from . import models


def post_init_hook(env):
    """Link historical POS receivable lines to their orders on a fresh install.

    Without this, every receivable line created before the module existed has an
    empty Date / Order / Service Type in the detailed report, because core never
    recorded which order a "pay later" payment belonged to. See
    pos_session._laundry_backfill_pos_order_links for how the link is rebuilt.

    Installs run this hook; upgrades of an already-installed database run
    migrations/1.2.0/post-migration.py instead. The backfill skips lines that
    already carry a pos_order_id, so running it twice is harmless.
    """
    env['pos.session']._laundry_backfill_pos_order_links()
