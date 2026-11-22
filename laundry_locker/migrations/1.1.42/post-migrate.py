"""Un-pad the locker codes Odoo invented for itself.

`_location_code` used to zero-pad every code to four digits, so Air's
`LX-002` was stored `LX-0002`. The feed, PudoPro and the dashboard all say
`LX-002`, which made Odoo the only place where that site had a different
name - and a site has to be looked up in two apps at once often enough for
that to matter. The padding is gone from the normalizer; this puts the rows
it already wrote right.

Needed even though the sync rewrites `location_code` whenever it differs: it
only ever sees the rows still in the feed, so a cleared or long-finished
drop-off would keep the padded code for good and the Location column would
show Air twice, under two spellings, forever.

Driven off LOCATION_NAMES rather than a hard-coded pair, so adding a locker
whose code the old padding would have mangled needs nothing here. Only a code
that really IS the padded form of a known one is touched - `LX-0001` is a
known code in its own right and is left alone.
"""

import logging

from odoo.addons.laundry_locker.models.laundry_locker_sync import LOCATION_NAMES

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    for code in LOCATION_NAMES:
        if not code.startswith('LX-'):
            continue
        digits = code[len('LX-'):]
        if not digits.isdigit():
            continue
        padded = 'LX-%04d' % int(digits)
        if padded == code:
            continue
        cr.execute(
            """
            UPDATE laundry_locker_transaction
               SET location_code = %s
             WHERE location_code = %s
            """,
            (code, padded),
        )
        if cr.rowcount:
            _logger.info(
                'laundry_locker: %s locker rows re-coded %s -> %s',
                cr.rowcount, padded, code,
            )
