"""Guarantee every existing drop-off stays visible when `active` arrives.

Odoo writes a new stored field's default onto existing rows itself, so this
should be a no-op. It is here because the one way it could fail is total: a
NULL `active` reads as archived, which would empty the locker list AND the
cashier's locker picker at once, on every row, silently - the sync would keep
running and nobody would see a drop-off again until someone thought to look
under a filter that did not exist yesterday.

Cheap insurance against that, and harmless if the ORM already did its job.
Only NULL is touched, so a row deliberately archived before this ran stays
archived.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE laundry_locker_transaction
           SET active = TRUE
         WHERE active IS NULL
    """)
