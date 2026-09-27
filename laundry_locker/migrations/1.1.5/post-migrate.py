"""Fill in the locker site on orders taken before the field existed.

The site is copied onto the order at the till, the way the door and the ref
are, so a receipt reprinted on a machine that never loaded the drop-off queue
still prints it. Orders billed BEFORE this version carry no site at all, and
nothing at reprint time can work one out - which would leave the site on new
tickets and missing from old ones, for no reason a cashier could see.

So it is filled in once, from the transaction each order is already keyed to.
Only where it is empty: a value a cashier has since corrected on the order is
the order's own and stands.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE pos_order o
           SET laundry_locker_location = t.location_name
          FROM laundry_locker_transaction t
         WHERE t.ref = o.laundry_locker_ref
           AND o.laundry_locker_ref IS NOT NULL
           AND t.location_name IS NOT NULL
           AND (o.laundry_locker_location IS NULL OR o.laundry_locker_location = '')
    """)
