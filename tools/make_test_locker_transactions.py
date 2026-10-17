"""Seed fake locker transactions on STAGING so the locker flow can be tested.

Locker transactions normally only ever arrive from the PudoPro feed - the list
and form both carry `create="0"`, because a drop-off invented by hand is a bag
that does not exist. That is right for the real database and useless for
testing, so this script writes a few directly.

Run it through `odoo shell`, which hands us `env`:

    docker exec -i odoo-odoo-staging-1 \
        odoo shell -c /etc/odoo/odoo.conf -d laundryx_staging --no-http \
        < tools/make_test_locker_transactions.py

It REFUSES to run on any database whose name does not end in `_staging`. The
guard is the whole reason this is a committed file rather than a command
pasted into a terminal: a paste has nothing to stop it being pointed at
`laundryx`, and these rows would then sit in the real locker queue offering a
cashier a bag nobody dropped off.

Nothing here touches Firebase. The feed is upserted and never reconciled, so a
ref that exists only in Odoo is simply never visited by the sync - these rows
will not be rewritten or removed by the 5-minute cron, and will not appear on
the dashboard. Re-running the script replaces the rows it made last time.

Delete them when you are done:

    env['laundry.locker.transaction'].search(
        [('ref', 'like', 'TEST-%')]).unlink()
"""

from datetime import timedelta

from odoo import fields

from odoo.addons.laundry_pos.models.res_partner import (
    is_placeholder_phone,
    phone_last10,
)

REF_PREFIX = 'TEST-'
SITE_CODE = 'RISE'
SITE_NAME = 'RISE'
# Not 'Cleared'. A cleared drop-off is deliberately filtered out of the POS
# picker (CLEARED_STATUS in laundry_locker_sync), so a test row that used it
# would simply never show up and look like a bug in the picker.
STATUS = 'New Laundry'


def unused_numbers(env, count):
    """`count` mobile numbers no contact holds, for the 'New customer' rows.

    Generated rather than hard-coded: the point of those rows is that the
    locker's number finds NOTHING in the customer book, and a number written
    into this file stops being unused the first time somebody tests with it
    and saves the contact.

    It also has to be a number the shop's own rules accept - the new contact
    is created through `res.partner.create`, which refuses a filler
    (is_placeholder_phone), so `0917 000 0000` would fail at the step the test
    is trying to reach.
    """
    found = []
    for n in range(1000):
        candidate = '0917%07d' % (5550000 + n)
        if is_placeholder_phone(candidate):
            continue
        key = phone_last10(candidate)
        # active_test=False because an ARCHIVED contact holds its number as
        # firmly as a live one - the duplicate rule counts both, so a number
        # that collides with an archived twin is not usable here either.
        if env['res.partner'].with_context(active_test=False).search_count(
            [('laundry_phone_last10', '=', key)]
        ):
            continue
        found.append(candidate)
        if len(found) == count:
            return found
    raise SystemExit('could not find %d unused test numbers' % count)


def returning_contact(env):
    """An existing contact with a real number, for the 'Returning' row.

    Read out of the database instead of named here, because which contacts
    exist depends on when staging was last refreshed from production.
    """
    for partner in env['res.partner'].search(
        [('phone', '!=', False), ('parent_id', '=', False)],
        order='id desc', limit=200,
    ):
        if partner.laundry_phone_last10 and not is_placeholder_phone(partner.phone):
            return partner
    return env['res.partner'].browse()


def main(env):
    db = env.cr.dbname
    if not db.endswith('_staging'):
        raise SystemExit(
            'REFUSED: %s is not a staging database. This script invents locker '
            'drop-offs; on a real database they would offer a cashier a bag '
            'that does not exist.' % db
        )

    model = env['laundry.locker.transaction']
    old = model.with_context(active_test=False).search([('ref', 'like', REF_PREFIX + '%')])
    if old:
        # Only ever the rows this script made. A test row that has since been
        # BILLED is left alone - it is attached to a real POS order now, and
        # deleting it would strip the locker reference off that sale.
        unbilled = old.filtered(lambda t: not t.billed)
        print('removing %d previous test row(s), keeping %d billed'
              % (len(unbilled), len(old) - len(unbilled)))
        unbilled.unlink()

    now = fields.Datetime.now()
    dropped = now - timedelta(hours=2)
    phone_one, phone_two = unused_numbers(env, 2)
    contact = returning_contact(env)

    rows = [
        {
            'ref': REF_PREFIX + 'NEW-1',
            'customer_name': 'TEST Walk-in One',
            'phone': phone_one,
            'service': 'Wash, Dry & Fold',
            'turnaround': 'Regular',
            'dirty_door': '12',
            # Unverified: the register must refuse to create a contact until
            # someone has called the number and heard it ring.
            'phone_verified': False,
            'pickup_datetime': now + timedelta(hours=2),
            'delivery_datetime': now + timedelta(days=1, hours=2),
        },
        {
            'ref': REF_PREFIX + 'NEW-2',
            'customer_name': 'TEST Walk-in Two',
            # A second unused number, so this row is a DIFFERENT new customer.
            'phone': phone_two,
            'service': 'Wash, Dry & Fold, Press',
            'turnaround': 'Express',
            'dirty_door': '07',
            # Verified, so this is the row that reaches contact creation - and
            # therefore the one to try a faked number on.
            'phone_verified': True,
            'pickup_datetime': now + timedelta(hours=3),
            'delivery_datetime': now + timedelta(hours=21),
        },
    ]

    if contact:
        rows.append({
            'ref': REF_PREFIX + 'RET-1',
            'customer_name': contact.name,
            # The contact's own number, so the match finds them and the row
            # reads as Returning.
            'phone': contact.phone,
            'service': 'Dry Clean',
            'turnaround': 'Regular',
            'dirty_door': '03',
            'phone_verified': True,
            'pickup_datetime': now + timedelta(hours=4),
            'delivery_datetime': now + timedelta(days=2),
        })

    created = model.browse()
    for row in rows:
        row.update({
            'location_code': SITE_CODE,
            'location_name': SITE_NAME,
            'status_code': STATUS,
            'status_label': STATUS,
            'created_at': dropped,
            'updated_at': dropped,
            # What the feed would have sent. Kept equal to the live values so
            # the sync's "did a cashier correct this?" test reads false and
            # nothing here looks hand-edited.
            'source_name': row['customer_name'],
            'source_phone': row['phone'],
            # The moment the bag became laundry. The turnaround on a Locker
            # order is measured from this, so it has to be set or the modal has
            # nothing to count from.
            'new_laundry_at': dropped,
            'billed': False,
            'active': True,
        })
        created |= model.create(row)

    env.cr.commit()

    print('')
    print('created %d test locker transaction(s) on %s:' % (len(created), db))
    for tx in created:
        print('  %-14s %-18s %-14s %-9s match=%-9s verified=%s'
              % (tx.ref, tx.customer_name, tx.phone, tx.turnaround,
                 tx.customer_match, tx.phone_verified))
    if not contact:
        print('  (no Returning row - no contact with a usable phone number found)')
    print('')
    print('They are now in the register: New Order -> Locker -> the picker.')


main(env)  # noqa: F821 - odoo shell provides `env`
