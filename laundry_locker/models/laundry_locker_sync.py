import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import pytz
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# The feed lives in the Firebase REALTIME DATABASE (not Firestore), and every
# node under it is world-readable by the database rules - so a pull is a plain
# GET with no credentials. Reads are billed on bandwidth, which is why the
# routine pull asks for a delta and not the whole node.
#
# SOURCE: /orders - the node behind the dashboard's LIST tab, written every 5
# minutes by pudopro_orders_sync.py. NOT /lockerTransactions, which backs the
# LOCKER tab and is only refreshed hourly from the PudoPro API. /orders is the
# fresher of the two and only ever gains a row once the drop-off has reached a
# status worth billing, which is exactly the queue the till needs. Both nodes
# key on the same LX- refs, so /lockerDoors and /lockerBilled line up with
# either one.
DEFAULT_FIREBASE_URL = (
    'https://pickup-delivery-c1df1-default-rtdb.asia-southeast1.firebasedatabase.app'
)

PARAM_URL = 'laundry_locker.firebase_url'
PARAM_TOKEN = 'laundry_locker.push_token'
PARAM_LAST_PULL = 'laundry_locker.last_pull_ms'

# The pull re-reads a little before its own watermark: the sync service writes
# in batches, so a record can land with an `updatedAt` just behind the moment we
# recorded.
PULL_OVERLAP_MS = 15 * 60 * 1000
REQUEST_TIMEOUT = 30

# /orders holds every channel, the orders Odoo itself pushes back included.
# Locker ones are marked, and their refs sit in their own prefix - which also
# gives a locker-only read needing no Firebase index, since $key is always
# indexed.
LOCKER_CHANNEL = 'locker'
LOCKER_REF_PREFIX = 'LX-'

# The schedule in the feed is a wall-clock date and hour at the locker, and
# every locker is in the Philippines - it is a property of the source, not of
# whoever is reading it in Odoo. It is converted once, on the way in, so
# everything downstream is an ordinary UTC-naive Odoo datetime.
FEED_TIMEZONE = 'Asia/Manila'


def _clean(value):
    """A feed value as a stripped string, or False for an empty one."""
    if value is None or value is False:
        return False
    text = str(value).strip()
    return text or False


def _ms_to_datetime(value):
    """Unix milliseconds (what the feed stores) to a UTC-naive datetime."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return False
    if ms <= 0:
        return False
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


def _feed_datetime(date_text, hour_text):
    """A feed `YYYY-MM-DD` date plus its hour-of-day, as a UTC-naive datetime.

    The hour arrives as a string ("21"), and a booking with a date but no hour
    is real - the customer picked a day and left the time to the shop - so a
    missing or unreadable hour falls back to midnight rather than losing the
    date with it.
    """
    date_text = _clean(date_text)
    if not date_text:
        return False
    try:
        day = datetime.strptime(str(date_text)[:10], '%Y-%m-%d')
    except ValueError:
        return False
    try:
        hour = int(float(str(hour_text).strip()))
    except (TypeError, ValueError, AttributeError):
        hour = 0
    if not 0 <= hour <= 23:
        hour = 0
    local = pytz.timezone(FEED_TIMEZONE).localize(day.replace(hour=hour))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


class LaundryLockerTransaction(models.Model):
    _inherit = 'laundry.locker.transaction'

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    @api.model
    def _firebase_base_url(self):
        url = self.env['ir.config_parameter'].sudo().get_param(PARAM_URL)
        return (url or DEFAULT_FIREBASE_URL).rstrip('/')

    @api.model
    def _push_token(self, generate=False):
        """The shared secret the locker service sends on a push.

        No token configured means the endpoint accepts nothing - an unset
        parameter must never read as "no authentication required".
        """
        icp = self.env['ir.config_parameter'].sudo()
        token = (icp.get_param(PARAM_TOKEN) or '').strip()
        if not token and generate:
            token = uuid.uuid4().hex
            icp.set_param(PARAM_TOKEN, token)
        return token

    @api.model
    def _check_push_token(self, candidate):
        expected = self._push_token()
        if not expected:
            return False
        return hmac.compare_digest(str(candidate or ''), expected)

    # ------------------------------------------------------------------
    # Firebase reads
    # ------------------------------------------------------------------
    @api.model
    def _firebase_get(self, path, query=None):
        url = '%s/%s.json' % (self._firebase_base_url(), path.strip('/'))
        if query:
            url += '?' + urlencode(query)
        request = Request(url, headers={'Accept': 'application/json'})
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read()
        return json.loads(body.decode('utf-8')) if body else None

    @api.model
    def _firebase_patch(self, path, payload):
        """Merge-write one node. PATCH, never PUT: siblings must survive."""
        url = '%s/%s.json' % (self._firebase_base_url(), path.strip('/'))
        body = json.dumps(payload).encode('utf-8')
        request = Request(
            url, data=body, method='PATCH',
            headers={'Content-Type': 'application/json'},
        )
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response.read()

    def _push_billed_to_firebase(self):
        """Write the billed state back for the dashboard to show.

        It goes to /lockerBilled/{ref}, NOT onto the order itself: the sync
        services own the rows they write and rebuild them wholesale, so
        anything we added inside one would be wiped on the next rebuild. Same
        reason /lockerFlags and /lockerDoors are their own nodes.

        Never fatal - Odoo is the source of truth for billing; this is the copy
        the dashboard reads.
        """
        for tx in self:
            if not tx.ref:
                continue
            payload = {
                'billed': bool(tx.billed),
                'at': self._now_ms(),
                'order': tx.pos_order_id.pos_reference or '',
            }
            try:
                self._firebase_patch('lockerBilled/%s' % quote(tx.ref, safe=''), payload)
            except Exception:  # noqa: BLE001
                _logger.warning(
                    'Locker: could not write back the billed state for %s', tx.ref,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # mapping
    # ------------------------------------------------------------------
    @api.model
    def _order_to_vals(self, record, doors=None):
        """One `/orders/{ref}` record as field values.

        The customer arrives as a nested block here, and there is no numeric
        status code: /orders carries the dashboard's own workflow state, which
        is what the LIST tab shows.
        """
        doors = doors or {}
        ref = str(record.get('id') or record.get('ref') or '').strip()
        customer = record.get('customer')
        customer = customer if isinstance(customer, dict) else {}
        time_block = record.get('time')
        time_block = time_block if isinstance(time_block, dict) else {}

        services = record.get('services')
        if isinstance(services, dict):
            services = list(services.values())
        if isinstance(services, (list, tuple)):
            service = ', '.join(str(s).strip() for s in services if s)
        else:
            service = _clean(services)

        # /orders carries no door of its own, so the latched drop-off door from
        # /lockerDoors is the only source for it. (The locker's live
        # `doorNumber` would be wrong anyway - once the clean laundry goes back
        # into another door it is no longer the drop-off door.)
        door = (doors.get(ref) or {}).get('d')
        name = _clean(customer.get('name'))
        phone = _clean(customer.get('contact'))
        return {
            'ref': ref,
            'location_code': _clean(record.get('locationCode')),
            # Older locker rows carry no location of their own; the customer
            # block's building is the same place under another name.
            'location_name': (
                _clean(record.get('locationName')) or _clean(customer.get('building'))
            ),
            'customer_name': name,
            'source_name': name,
            'phone': phone,
            'source_phone': phone,
            'service': service or False,
            'turnaround': _clean(time_block.get('turnaround')),
            'pickup_datetime': _feed_datetime(
                time_block.get('pickupDate'), time_block.get('pickupHour')
            ),
            'delivery_datetime': _feed_datetime(
                time_block.get('deliveryDate'), time_block.get('deliveryHour')
            ),
            'dirty_door': _clean(door),
            'status_code': _clean(record.get('status')),
            'status_label': (
                _clean(record.get('overallStatus')) or _clean(record.get('status'))
            ),
            'created_at': _ms_to_datetime(record.get('createdAt')),
            'updated_at': _ms_to_datetime(record.get('updatedAt')),
            # One and the same instant on this feed: a row is only written once
            # PudoPro has moved the transaction to New Laundry, and its
            # createdAt matches that milestone exactly.
            'new_laundry_at': _ms_to_datetime(record.get('createdAt')),
        }

    def _sync_writes(self, vals):
        """Narrow incoming values to what this record should actually take.

        Two things the sync must not undo: a phone number (or name) a cashier
        corrected, because putting the customer's typo back is the whole failure
        this feature exists to stop; and anything Odoo owns - billed,
        pos_order_id, phone_verified and a hand-set partner_id are never in
        `vals` to begin with.
        """
        self.ensure_one()
        writes = dict(vals)
        if self.source_phone and self.phone != self.source_phone:
            writes.pop('phone', None)
        if self.source_name and self.customer_name != self.source_name:
            writes.pop('customer_name', None)
        return {k: v for k, v in writes.items() if (self[k] or False) != (v or False)}

    @api.model
    def _locker_orders(self, records):
        """The locker rows out of an /orders payload, each carrying its ref.

        Firebase hands a node back as an object keyed by ref, so the key is
        injected as `id` wherever the record does not already carry one.
        Everything that is not a locker drop-off is dropped here: /orders holds
        every channel, the dropoff/delivery orders Odoo itself pushes included.
        """
        if isinstance(records, dict):
            records = [
                dict(record, id=record.get('id') or key)
                for key, record in records.items()
                if isinstance(record, dict)
            ]
        rows = []
        for record in (records or []):
            if not isinstance(record, dict):
                continue
            ref = str(record.get('id') or record.get('ref') or '').strip()
            if not ref:
                continue
            # The prefix is the belt to the channel's braces: the oldest locker
            # rows predate the channel marker, and only lockers use LX-.
            if _clean(record.get('channel')) == LOCKER_CHANNEL or ref.startswith(
                LOCKER_REF_PREFIX
            ):
                rows.append(record)
        return rows

    @api.model
    def _upsert_orders(self, records, doors=None):
        """Create or update transactions from /orders rows, keyed on `ref`.

        Shared by both inbound paths - the locker service's push and the hourly
        pull - so they cannot drift apart.
        """
        records = self._locker_orders(records)
        if not records:
            return self.browse()

        # A ref can appear twice in one payload; the last one wins.
        by_ref = {}
        for record in records:
            vals = self._order_to_vals(record, doors)
            if vals['ref']:
                by_ref[vals['ref']] = vals

        existing = {
            tx.ref: tx for tx in self.search([('ref', 'in', list(by_ref))])
        }
        now = fields.Datetime.now()
        touched = self.browse()
        to_create = []
        for ref, vals in by_ref.items():
            vals['synced_at'] = now
            tx = existing.get(ref)
            if tx:
                writes = tx._sync_writes(vals)
                if writes:
                    tx.write(writes)
                touched |= tx
            else:
                to_create.append(vals)
        if to_create:
            touched |= self.create(to_create)
        return touched

    # ------------------------------------------------------------------
    # the pull (safety net behind the push)
    # ------------------------------------------------------------------
    @api.model
    def _sync_pull(self, since_ms=None, full=False):
        icp = self.env['ir.config_parameter'].sudo()
        if not full and since_ms is None:
            last = icp.get_param(PARAM_LAST_PULL)
            if last:
                try:
                    since_ms = max(int(last) - PULL_OVERLAP_MS, 0)
                except ValueError:
                    since_ms = None

        query = None
        if since_ms and not full:
            # Firebase wants the key quoted inside the query value.
            query = {'orderBy': '"updatedAt"', 'startAt': int(since_ms)}
        try:
            records = self._firebase_get('orders', query)
        except HTTPError as err:
            # The delta query needs `".indexOn": ["updatedAt"]` on /orders in
            # the Firebase rules. Without it Firebase 400s - fall back to a
            # read by key, which needs no index at all ($key is always indexed)
            # and still reads only the locker range rather than every order.
            if query and err.code == 400:
                _logger.warning(
                    'Locker sync: indexed delta read refused (%s). Falling back to a '
                    'read of the whole LX- range - add ".indexOn": ["updatedAt"] '
                    'to /orders.',
                    err,
                )
                records = self._firebase_get('orders', {
                    'orderBy': '"$key"',
                    'startAt': json.dumps(LOCKER_REF_PREFIX),
                    # \uf8ff is above every character Firebase will sort, so
                    # this ends the range at the last LX- key.
                    'endAt': json.dumps(LOCKER_REF_PREFIX + '\uf8ff'),
                })
            else:
                raise

        records = self._locker_orders(records)
        if not records:
            icp.set_param(PARAM_LAST_PULL, str(self._now_ms()))
            return self.browse()

        doors = {}
        try:
            doors = self._firebase_get('lockerDoors') or {}
        except Exception:  # noqa: BLE001 - the door latch is an overlay, not the data
            _logger.warning('Locker sync: could not read /lockerDoors', exc_info=True)

        touched = self._upsert_orders(records, doors if isinstance(doors, dict) else {})

        # Watermark from the data, not the clock: a record written while this
        # pull was running is then still in range of the next one. It counts
        # only locker rows, so a busy shop's other orders can never carry the
        # mark past a drop-off we have not read.
        seen = [r.get('updatedAt') for r in records]
        highest = max((int(v) for v in seen if isinstance(v, (int, float))), default=0)
        icp.set_param(PARAM_LAST_PULL, str(highest or self._now_ms()))
        return touched

    @api.model
    def _now_ms(self):
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    @api.model
    def _cron_sync_locker_transactions(self):
        """Hourly safety net: catches anything the push missed or arrived for
        while Odoo was down, and every later status change."""
        self._sync_pull()

    def action_full_resync(self):
        """Re-read the WHOLE feed, ignoring the delta watermark.

        The routine pull only asks for what changed since it last ran, so a
        field added to the mapping after a record was imported stays empty on
        that record forever. This is how those get filled in. It is the
        expensive read - a button, not a cron.
        """
        return self.action_sync_now(full=True)

    def action_sync_now(self, full=False):
        try:
            touched = self._sync_pull(full=full)
        except Exception as err:  # noqa: BLE001 - shown to the cashier, not swallowed
            _logger.exception('Locker sync failed')
            raise UserError(_('Could not reach the locker feed:\n%s') % err) from err
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Locker sync'),
                'message': _('%s transaction(s) synced.') % len(touched),
                'type': 'success',
                'sticky': False,
            },
        }
