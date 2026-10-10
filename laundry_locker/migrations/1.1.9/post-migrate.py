"""Give back the drop-offs v1.1.8 archived for being cleared.

v1.1.8 could only find a clear by looking up PudoPro's /lockerTransactions
node one ref at a time, and it took the row out of the cashier's picker by
ARCHIVING it - the only lever that worked without touching the picker's query.

/orders now carries the clear itself, so v1.1.9 reads it straight off the feed
and the picker filters on `locker_cleared` directly. Archiving is no longer
part of it, and a cleared drop-off belongs back on the list with "Cleared" in
its Status column rather than hidden behind a filter.

Only rows this module archived for a clear are touched. A drop-off archived
because it was DELETED at the locker has `locker_cleared` false and stays
archived, which is still the only way a deletion can be recorded - the feed
cannot report a row that is gone from it.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE laundry_locker_transaction
           SET active = TRUE
         WHERE active = FALSE
           AND locker_cleared = TRUE
    """)
