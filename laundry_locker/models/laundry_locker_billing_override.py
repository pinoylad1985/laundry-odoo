from datetime import datetime

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# The shop's own clock. Imported rather than repeated: it is the same place
# the feed's times are in, and two spellings of one timezone would drift.
from .laundry_locker_sync import FEED_TIMEZONE

# The shapes the cutoff may be written in, longest first. A bare date is the
# one to reach for - it is the whole point that this reads like a date.
CUTOFF_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d')

# The date before which every locker drop-off was already sold at the till.
#
# Deliberately a system parameter and not a screen: it states one historical
# fact - when this shop started billing lockers through Odoo - and it is set
# once, in Settings > Technical > System Parameters, as
# `laundry_locker.billed_before`. A screen would invite it to be adjusted,
# and moving it re-decides hundreds of rows at a stroke.
#
# Written as `YYYY-MM-DD` in SHOP time, not UTC. It is compared against the
# New Laundry column, and that column is read off the screen in shop time -
# a cutoff that quietly meant 8am would put a day's drop-offs on the wrong
# side of the line it was supposed to draw.
PARAM_BILLED_BEFORE = 'laundry_locker.billed_before'


class LaundryLockerBillingOverride(models.Model):
    """A standing decision about one ref, re-applied on every sync.

    The locker list is disposable - deleting its rows and resyncing rebuilds
    them from the feed - and billing normally survives that on its own,
    because the row asks the ORDERS about itself and a sale carries
    `laundry_locker_ref` for life (see _laundry_adopt_billed_orders).

    What has no order to be asked about is a drop-off sold OUTSIDE Odoo:
    everything from before the till was doing lockers at all, and the
    occasional one settled some other way since. Stating that as a one-off
    write lasted exactly until the next rebuild, and then the counter had
    hundreds of long-collected bags back in its queue.

    So it is stated here instead, and re-applied every time. Two directions,
    because the cutoff needs exceptions as much as it needs additions:
    `billed` says this ref was sold even though no order shows it, and `hold`
    says this one was NOT, despite falling before the cutoff.
    """

    _name = 'laundry.locker.billing.override'
    _description = 'Locker Billing Override'
    _order = 'treatment, ref'

    ref = fields.Char(
        string='Ref #', required=True, index=True,
        help='The locker transaction reference, e.g. LX-9nXDX. '
             'Case does not matter.',
    )
    treatment = fields.Selection(
        [('billed', 'Sold - mark it billed'),
         ('hold', 'Not sold - keep it unbilled')],
        string='Treatment', required=True, default='billed',
        help='Sold: this drop-off was paid for outside Odoo, so it should '
             'never appear in the queue.\n'
             'Not sold: this one is still to be billed, even though it is '
             'older than the cutoff date.',
    )
    # Required on purpose. A year from now the ref alone says nothing about
    # why somebody decided this, and the decision outlives the person.
    reason = fields.Char(
        string='Reason', required=True,
        help='Why this drop-off is not being treated the way the orders and '
             'the cutoff date would treat it.',
    )
    active = fields.Boolean(
        string='Active', default=True,
        help='Untick to retire this decision without losing the record of '
             'having made it. It stops being applied at the next sync.',
    )
    transaction_id = fields.Many2one(
        'laundry.locker.transaction', string='In the list',
        compute='_compute_transaction_id',
        help='The locker transaction this ref currently matches. Empty means '
             'no such row is in the list right now - which is not a problem: '
             'the decision simply waits, and applies if the ref turns up.',
    )
    transaction_billed = fields.Boolean(
        string='Billed now', related='transaction_id.billed', readonly=True,
    )

    @api.depends('ref')
    def _compute_transaction_id(self):
        transactions = self.env['laundry.locker.transaction']
        for rule in self:
            rule.transaction_id = transactions.search(
                [('ref', '=ilike', rule.ref)], limit=1,
            ) if rule.ref else transactions

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('ref'):
                vals['ref'] = vals['ref'].strip()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('ref'):
            vals['ref'] = vals['ref'].strip()
        return super().write(vals)

    @api.constrains('ref')
    def _check_ref_unique(self):
        """One decision per ref, whatever case it was typed in.

        Two rows disagreeing about the same ref would be settled by whichever
        the search happened to return second - a coin toss over whether a
        drop-off sits in the queue.
        """
        # Archived rows count: a retired decision and a live one on the same
        # ref is the confusing case, not a safe one.
        others = self.with_context(active_test=False).search(
            [('id', 'not in', self.ids)],
        )
        taken = {(rule.ref or '').strip().lower() for rule in others}
        seen = set()
        for rule in self:
            key = (rule.ref or '').strip().lower()
            if key in taken or key in seen:
                raise ValidationError(
                    _('There is already a billing override for %s.', rule.ref)
                )
            seen.add(key)

    # ------------------------------------------------------------------
    # what the sync asks
    # ------------------------------------------------------------------
    @api.model
    def _laundry_billed_before(self):
        """The cutoff as a UTC-naive datetime, or None if none is set.

        Unset is the safe state - no cutoff means nothing is billed for being
        old, and the list behaves exactly as it did before this existed. So is
        unreadable: a mistyped parameter leaves drop-offs in the queue, where
        somebody will notice them, rather than billing a sweep of them on a
        date nobody meant.

        The value is read as SHOP time and converted here, so that what is
        written in the parameter is what the New Laundry column shows. A bare
        date means midnight that morning.
        """
        raw = (self.env['ir.config_parameter'].sudo()
               .get_param(PARAM_BILLED_BEFORE) or '').strip()
        if not raw:
            return None
        for pattern in CUTOFF_FORMATS:
            try:
                local = datetime.strptime(raw, pattern)
            except ValueError:
                continue
            return pytz.timezone(FEED_TIMEZONE).localize(local).astimezone(
                pytz.utc).replace(tzinfo=None)
        return None

    @api.model
    def _laundry_rules(self):
        """Every live decision, keyed by ref folded to lower case.

        One read for the whole sync: there are tens of these at most, against
        a list of thousands, so they are fetched once and matched in memory
        rather than queried per row.
        """
        rules = {}
        for rule in self.sudo().search([]):
            key = (rule.ref or '').strip().lower()
            if key:
                rules[key] = rule.treatment
        return rules
