"""Re-run the customer match now that it can see ARCHIVED contacts.

The match (_compute_partner_id) searched active contacts only, while the
one-customer-per-number rule in res.partner counts archived ones - so a
drop-off whose number is held by an archived contact read "New", the cashier
called it, keyed a name, and Confirm was refused flat by the server. The
lookup now sees them, and the picker offers to restore the contact.

The compute depends on the phone, which nothing here changes, so rows already
in the book would keep their stale answer until their number moved. This asks
the question again for every drop-off it can still matter to: unbilled, and
still matched to nobody. A row that already has a customer is left alone, as
it is everywhere else in this module - a match a cashier corrected by hand is
never overwritten.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    pending = env['laundry.locker.transaction'].search([
        ('billed', '=', False),
        ('partner_id', '=', False),
        ('phone_last10', '!=', False),
    ])
    if not pending:
        return
    pending._laundry_rematch_partner()
    pending.flush_recordset()
