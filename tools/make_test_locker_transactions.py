"""Seed fake locker transactions on STAGING so the locker flow can be tested.

Locker transactions normally only ever arrive from the PudoPro feed - the list
and form both carry `create="0"`, because a drop-off invented by hand is a bag
that does not exist. That is right for the real database and useless for
testing, so this script writes a few directly.

Run it through `odoo shell`, which hands us `env`:

    cd /opt/odoo && docker compose run --rm -T odoo-staging \
        odoo shell -d laundryx_staging --no-http \
        < tools/make_test_locker_transactions.py

NOT through `docker exec`. The image entrypoint is what turns the compose
environment into Odoo's --db_host/--db_user/--db_password arguments, and
`docker exec` runs straight past it: Odoo then looks for Postgres on a unix
socket inside its own container and dies with "database: default@default".
`compose run` goes through the entrypoint - which is why the deploy script
uses it too.

It REFUSES to run on any database whose name does not end in `_staging`. The
guard is the whole reason this is a committed file rather than a command
pasted into a terminal: a paste has nothing to stop it being pointed at
`laundryx`, and these rows would then sit in the real locker queue offering a
cashier a bag nobody dropped off.

Nothing here touches Firebase. The feed is upserted and never reconciled, so a
ref that exists only in Odoo is simply never visited by the sync - these rows
will not be rewritten or removed by the 5-minute cron, and will not appear on
the dashboard.

RE-RUNNING IS THE NORMAL WAY TO START A FRESH ROUND. It drops the rows it made
last time (keeping any that have since been billed, which belong to a real POS
order now) AND the contacts those rows caused - a test that creates a contact
leaves the number no longer new, so without that cleanup the second round of
testing is not testing the same thing. Only contacts named with this script's
own prefix, and only ones nothing has used.

Delete everything by hand when you are done:

    env['laundry.locker.transaction'].search(
        [('ref', 'like', 'TEST-%')]).unlink()
    env['res.partner'].search([('name', 'like', 'TEST Locker %')]).unlink()
    env.cr.commit()
"""

from datetime import timedelta

from odoo import fields

from odoo.addons.laundry_pos.models.res_partner import (
    is_placeholder_phone,
    phone_last10,
)

REF_PREFIX = 'TEST-'
# Every contact these rows create inherits this name, which is what makes the
# cleanup above able to find them. Keep the two in step.
NAME_PREFIX = 'TEST Locker '
SITE_CODE = 'RISE'
SITE_NAME = 'RISE'
# Not 'Cleared'. A cleared drop-off is deliberately filtered out of the POS
# picker (CLEARED_STATUS in laundry_locker_sync), so a test row that used it
# would simply never show up and look like a bug in the picker.
STATUS = 'New Laundry'
# Numbers printed at the end for the tester to TYPE into a correction. They
# have to be unused for the same reason the rows' own numbers do, and they
# have to be reserved here rather than invented at the keyboard - a number
# guessed at the register may well belong to somebody, and the correction
# would then take the "matched an existing customer" branch instead of the
# one being tested.
SPARE_COUNT = 3


def unused_numbers(env, count):
    """`count` mobile numbers no contact holds.

    Generated rather than hard-coded: the point of the New rows is that the
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
    """An existing contact with a real number.

    Used twice: as the number on the Returning row, and as the number the
    tester types into the "corrected onto a customer already in the book"
    test. Read out of the database instead of named here, because which
    contacts exist depends on when staging was last refreshed from production.
    """
    for partner in env['res.partner'].search(
        [('phone', '!=', False), ('parent_id', '=', False)],
        order='id desc', limit=200,
    ):
        if (partner.laundry_phone_last10
                and not is_placeholder_phone(partner.phone)
                and not partner.name.startswith(NAME_PREFIX)):
            return partner
    return env['res.partner'].browse()


def clear_previous(env):
    """Drop last round's rows, and the contacts last round's testing created.

    The contacts matter as much as the rows. Claiming a New drop-off creates a
    contact on that number, so the number is no longer new - run the script
    again without clearing and the "a number nobody has" tests quietly become
    "a number somebody has" tests, which pass for the wrong reason.

    Narrow on both sides: rows this script made and has not had billed, and
    contacts carrying this script's name prefix that nothing has used.
    """
    model = env['laundry.locker.transaction']
    rows = model.with_context(active_test=False).search(
        [('ref', 'like', REF_PREFIX + '%')]
    )
    unbilled = rows.filtered(lambda t: not t.billed)
    kept = len(rows) - len(unbilled)
    if unbilled:
        unbilled.unlink()

    partners = env['res.partner'].with_context(active_test=False).search(
        [('name', 'like', NAME_PREFIX + '%')]
    )
    removed = 0
    stuck = []
    for partner in partners:
        # A test contact that ended up on a real sale is not cleanup any more.
        if env['pos.order'].search_count([('partner_id', '=', partner.id)]):
            stuck.append(partner.name)
            continue
        if env['account.move'].search_count([('partner_id', '=', partner.id)]):
            stuck.append(partner.name)
            continue
        try:
            partner.unlink()
            removed += 1
        except Exception:
            stuck.append(partner.name)
    return len(unbilled), kept, removed, stuck


def main(env):
    db = env.cr.dbname
    if not db.endswith('_staging'):
        raise SystemExit(
            'REFUSED: %s is not a staging database. This script invents locker '
            'drop-offs; on a real database they would offer a cashier a bag '
            'that does not exist.' % db
        )

    dropped_rows, kept_rows, dropped_partners, stuck = clear_previous(env)
    print('cleanup: removed %d row(s) and %d test contact(s); kept %d billed'
          % (dropped_rows, dropped_partners, kept_rows))
    for name in stuck:
        print('  kept contact (used by an order or invoice): %s' % name)

    model = env['laundry.locker.transaction']
    now = fields.Datetime.now()
    deposited = now - timedelta(hours=2)
    numbers = unused_numbers(env, 3 + SPARE_COUNT)
    row_numbers, spares = numbers[:3], numbers[3:]
    contact = returning_contact(env)

    rows = [
        {
            'ref': REF_PREFIX + 'CALL',
            'customer_name': NAME_PREFIX + 'Call Gate',
            'phone': row_numbers[0],
            'service': 'Wash, Dry & Fold',
            'turnaround': 'Regular',
            'dirty_door': '12',
            # Unverified, like every row the feed sends: the two-button step
            # is the thing being tested, so nothing may be answered already.
            'phone_verified': False,
            'pickup_datetime': now + timedelta(hours=2),
            'delivery_datetime': now + timedelta(days=1, hours=2),
        },
        {
            'ref': REF_PREFIX + 'FREE',
            'customer_name': NAME_PREFIX + 'Correct To Free',
            'phone': row_numbers[1],
            'service': 'Wash, Dry & Fold, Press',
            'turnaround': 'Express',
            'dirty_door': '07',
            'phone_verified': False,
            'pickup_datetime': now + timedelta(hours=3),
            'delivery_datetime': now + timedelta(hours=21),
        },
        {
            'ref': REF_PREFIX + 'TWIN',
            'customer_name': NAME_PREFIX + 'Correct To Customer',
            'phone': row_numbers[2],
            'service': 'Dry Clean',
            'turnaround': 'Regular',
            'dirty_door': '03',
            'phone_verified': False,
            'pickup_datetime': now + timedelta(hours=4),
            'delivery_datetime': now + timedelta(days=2),
        },
    ]

    if contact:
        rows.append({
            'ref': REF_PREFIX + 'RET',
            'customer_name': contact.name,
            # The contact's own number, so the match finds them and the row
            # reads as Returning with no gate at all.
            'phone': contact.phone,
            'service': 'Wash, Dry & Fold',
            'turnaround': 'Regular',
            'dirty_door': '05',
            'phone_verified': True,
            'pickup_datetime': now + timedelta(hours=5),
            'delivery_datetime': now + timedelta(days=1, hours=5),
        })

    created = model.browse()
    for row in rows:
        row.update({
            'location_code': SITE_CODE,
            'location_name': SITE_NAME,
            'status_code': STATUS,
            'status_label': STATUS,
            'created_at': deposited,
            'updated_at': deposited,
            # What the feed would have sent. Kept equal to the live values so
            # the sync's "did a cashier correct this?" test reads false and
            # nothing here looks hand-edited.
            'source_name': row['customer_name'],
            'source_phone': row['phone'],
            # The moment the bag became laundry. The turnaround on a Locker
            # order is measured from this, so it has to be set or the modal
            # has nothing to count from.
            'new_laundry_at': deposited,
            'billed': False,
            'active': True,
        })
        created |= model.create(row)

    env.cr.commit()

    print('')
    print('created %d test locker drop-off(s) on %s:' % (len(created), db))
    for tx in created:
        print('  %-11s %-32s %-15s %-8s match=%s'
              % (tx.ref, tx.customer_name, tx.phone, tx.turnaround,
                 tx.customer_match))
    print('')
    print('TYPE THESE when a test asks you to correct a number:')
    for n, spare in enumerate(spares, start=1):
        print('  spare %d (belongs to nobody)  %s' % (n, spare))
    if contact:
        print('  existing customer            %s   (%s)'
              % (contact.phone, contact.name))
    else:
        print('  (no existing contact with a usable number was found, so the')
        print('   TEST-RET row and the "correct onto a customer" test are out)')
    print('')
    print('They are in the register now: New Order -> Locker -> the picker.')


main(env)  # noqa: F821 - odoo shell provides `env`
