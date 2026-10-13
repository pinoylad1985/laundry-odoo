"""Drop the two Google Tasks columns with the feature that put them there.

The pickup/delivery buttons wrote a Google Task per drop-off and kept its id
on the row so a second press edited that task instead of making another one.
The feature is gone (it only ever existed on staging, and Google has no way
to share a task list, so the drivers it was for could never have been given
them), and with it the only thing that ever read or wrote these ids.

Odoo leaves a removed field's column in place, so this drops them - otherwise
they sit there holding ids for tasks nothing maintains, for the next person to
find and believe. The tasks themselves stay in Google; nothing here can reach
them any more, and they are ticked off or deleted by hand.

Guarded on each column existing: a database that never ran the staging-only
feature has neither, and an unguarded DROP would abort its upgrade over
nothing to do.
"""


def migrate(cr, version):
    if not version:
        return
    for column in ('google_pickup_task_id', 'google_delivery_task_id'):
        cr.execute("""
            SELECT 1
              FROM information_schema.columns
             WHERE table_name = 'laundry_locker_transaction'
               AND column_name = %s
        """, (column,))
        if cr.fetchone():
            cr.execute(
                'ALTER TABLE laundry_locker_transaction DROP COLUMN "%s"' % column
            )
