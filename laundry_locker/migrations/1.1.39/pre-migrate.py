"""Move the corrections already made into the two new columns.

Until now a cashier's correction was written STRAIGHT OVER `customer_name` /
`phone`, and the feed's own values were kept in a second pair of hidden
columns, `source_name` / `source_phone`, purely so the sync could ask "did
somebody correct this?" and leave the row alone if so. The two pairs have now
swapped jobs: `customer_name` / `phone` hold the locker's own values and are
never written over, and `customer_name_corrected` / `phone_corrected` hold the
shop's note against them. Both pairs are columns on the Lockers list, which is
what the owner asked for and what the hidden pair could never be.

So every row where the two disagreed is carrying a correction in the wrong
column, and this puts it where it now belongs - otherwise the upgrade would
read those rows as if the customer had keyed in the corrected value themselves,
and the record of what was really typed would be lost with the dropped
columns.

Runs PRE-migrate because `source_*` still exists here: the ORM drops a removed
field's data during module-data cleanup afterwards. The two new columns do NOT
exist yet at this point (the ORM creates them later in the same upgrade), so
they are added first - `IF NOT EXISTS`, since a re-run of the upgrade would
find them already there.

The test is `<>` on the stored text, which is the sync's own old guard verbatim:
whatever it called a correction then is a correction now, and nothing is
re-judged by the new digits/whitespace rules. A row those rules would call a
no-op gets cleared the next time it is saved.

`phone_last10` needs no recompute. It was keyed on `phone` and is now keyed on
the correction falling back to `phone`, and this swap leaves the number in
force exactly as it was.

ONE case cannot be carried over: a correction to BLANK. The old scheme could
represent it (`phone` emptied while `source_phone` held the feed's value), the
new one cannot - a blank correction column means "nothing was corrected". Such
a row keeps the locker's own value, which is the better of the two readings: a
drop-off with no number at all is one nothing can be done with, and the number
the customer typed is at least something to call.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'laundry_locker_transaction'
           AND column_name = 'source_phone'
    """)
    if not cr.fetchone():
        # A database that never had the old pair has nothing to move.
        return

    cr.execute("""
        ALTER TABLE laundry_locker_transaction
          ADD COLUMN IF NOT EXISTS customer_name_corrected varchar,
          ADD COLUMN IF NOT EXISTS phone_corrected varchar
    """)

    cr.execute("""
        UPDATE laundry_locker_transaction
           SET phone_corrected = phone,
               phone = source_phone
         WHERE source_phone IS NOT NULL
           AND source_phone <> ''
           AND phone IS NOT NULL
           AND phone <> ''
           AND phone <> source_phone
    """)
    moved_phones = cr.rowcount

    cr.execute("""
        UPDATE laundry_locker_transaction
           SET customer_name_corrected = customer_name,
               customer_name = source_name
         WHERE source_name IS NOT NULL
           AND source_name <> ''
           AND customer_name IS NOT NULL
           AND customer_name <> ''
           AND customer_name <> source_name
    """)
    moved_names = cr.rowcount

    # A row whose value was corrected to BLANK: the locker's own value is put
    # back, because a drop-off with no name or number is one nobody can act on.
    cr.execute("""
        UPDATE laundry_locker_transaction
           SET phone = source_phone
         WHERE (phone IS NULL OR phone = '')
           AND source_phone IS NOT NULL
           AND source_phone <> ''
    """)
    restored_phones = cr.rowcount

    cr.execute("""
        UPDATE laundry_locker_transaction
           SET customer_name = source_name
         WHERE (customer_name IS NULL OR customer_name = '')
           AND source_name IS NOT NULL
           AND source_name <> ''
    """)
    restored_names = cr.rowcount

    _logger.info(
        'laundry_locker 1.1.39: moved %s phone and %s name correction(s) into '
        'the new columns; restored %s blanked phone(s) and %s blanked name(s)',
        moved_phones, moved_names, restored_phones, restored_names,
    )
