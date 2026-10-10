"""Give back the drop-offs v1.1.8 archived for being cleared, and drop the flag.

v1.1.8 could only find a clear by looking up PudoPro's /lockerTransactions
node one ref at a time, and it took the row out of the cashier's picker by
ARCHIVING it - the only lever that worked without touching the picker's query.

/orders now carries the clear itself, as `overallStatus: "Cleared"`, so the
picker and the To Bill filter compare that status directly (see CLEARED_STATUS
in models/laundry_locker_sync.py). A cleared drop-off belongs back on the list,
with "Cleared" in its Status column, rather than hidden behind a filter.

The `locker_cleared` boolean that briefly stood between the two is gone with
it: it said nothing the status did not, and it showed up as a field of its own
in every Custom Filter dialog. Odoo leaves a removed field's column in place,
so this drops it - otherwise it sits there holding a value nothing maintains,
for the next person to find and believe.

Only rows this module archived for a CLEAR are un-archived. A drop-off archived
because it was DELETED at the locker has the flag false and stays archived,
which is still the only way a deletion can be recorded - the feed cannot report
a row that is gone from it.
"""


def migrate(cr, version):
    if not version:
        return
    # The column exists on any database that ran v1.1.8, and on none that went
    # from v1.1.7 straight to here. Checked rather than assumed because getting
    # it wrong aborts the upgrade on a database that has nothing to fix.
    cr.execute("""
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'laundry_locker_transaction'
           AND column_name = 'locker_cleared'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE laundry_locker_transaction
           SET active = TRUE
         WHERE active = FALSE
           AND locker_cleared = TRUE
    """)
    cr.execute("""
        ALTER TABLE laundry_locker_transaction
         DROP COLUMN locker_cleared
    """)
