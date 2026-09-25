import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# The PudoPro snapshot lives in the Firebase REALTIME DATABASE (not Firestore),
# and every node under it is world-readable by the database rules - so a pull is
# a plain GET with no credentials. Reads are billed on bandwidth, which is why
# the routine pull asks for a delta and not the whole node.
DEFAULT_FIREBASE_URL = (
    'https://pickup-delivery-c1df1-default-rtdb.asia-southeast1.firebasedatabase.app'
)

PARAM_URL = 'laundry_locker.firebase_url'
PARAM_TOKEN = 'laundry_locker.push_token'
PARAM_LAST_PULL = 'laundry_locker.last_pull_ms'

# The pull re-reads a little before its own watermark: the snapshot service
# writes in batches, so a record can land with an `updatedAt` just behind the
# moment we recorded.
PULL_OVERLAP_MS = 15 * 60 * 1000
REQUEST_TIMEOUT = 30


def _clean(value):
    """A snapshot value as a stripped string, or False for an empty one."""
    if value is None or value is False:
        return False
    text = str(value).strip()
    return text or False


def _status_text(value):
    """Status codes are 1.5 / 2.5 / 2.8 as well as 1 / 2 - keep them as text.

    JSON hands us 1 as `1` and 1.5 as `1.5`, and `str(1.0)` would be "1.0",
    which matches nothing in STATUS_LABELS.
    """
    if value is None or value is False or value == '':
        return False
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or False


def _ms_to_datetime(value):
    """Unix milliseconds (what the snapshot stores) to a UTC-naive datetime."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return False
    if ms <= 0:
        return False
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


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

    # ------------------------------------------------------------------
    # mapping
    # ------------------------------------------------------------------
    @api.model
    def _snapshot_to_vals(self, record, doors=None):
        """One `/lockerTransactions/{ref}` record as field values."""
        doors = doors or {}
        ref = str(record.get('ref') or '').strip()
        # `doorNumber` is where the parcel is RIGHT NOW, so once the clean
        # laundry goes back into another door it is no longer the drop-off
        # door. `/lockerDoors/{ref}.d` is the latched dirty door and wins.
        door = (doors.get(ref) or {}).get('d') or record.get('doorNumber')
        name = _clean(record.get('name'))
        phone = _clean(record.get('phone'))
        return {
            'ref': ref,
            'location_code': _clean(record.get('locationCode')),
            'location_name': _clean(record.get('locationName')),
            'customer_name': name,
            'source_name': name,
            'phone': phone,
            'source_phone': phone,
            'service': _clean(record.get('service')) or _clean(record.get('serviceType')),
            'turnaround': _clean(record.get('turnaround')),
            'dirty_door': _clean(door),
            'status_code': _status_text(record.get('statusCode')),
            'created_at': _ms_to_datetime(record.get('createdAt')),
            'updated_at': _ms_to_datetime(record.get('updatedAt')),
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
    def _upsert_snapshot(self, records, doors=None):
        """Create or update transactions from snapshot records, keyed on `ref`.

        Shared by both inbound paths - the locker service's push and the hourly
        pull - so they cannot drift apart.
        """
        if isinstance(records, dict):
            records = list(records.values())
        records = [r for r in (records or []) if isinstance(r, dict) and r.get('ref')]
        if not records:
            return self.browse()

        # A ref can appear twice in one payload; the last one wins.
        by_ref = {}
        for record in records:
            vals = self._snapshot_to_vals(record, doors)
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
            records = self._firebase_get('lockerTransactions', query)
        except HTTPError as err:
            # The delta query needs `".indexOn": ["updatedAt"]` on
            # /lockerTransactions in the Firebase rules. Without it Firebase
            # 400s - fall back to the whole node rather than sync nothing, and
            # say so, because that fallback is the expensive read.
            if query and err.code == 400:
                _logger.warning(
                    'Locker sync: indexed delta read refused (%s). Falling back to a '
                    'full read - add ".indexOn": ["updatedAt"] to /lockerTransactions.',
                    err,
                )
                records = self._firebase_get('lockerTransactions')
            else:
                raise

        if isinstance(records, dict):
            records = list(records.values())
        records = [r for r in (records or []) if isinstance(r, dict)]
        if not records:
            icp.set_param(PARAM_LAST_PULL, str(self._now_ms()))
            return self.browse()

        doors = {}
        try:
            doors = self._firebase_get('lockerDoors') or {}
        except Exception:  # noqa: BLE001 - the door latch is an overlay, not the data
            _logger.warning('Locker sync: could not read /lockerDoors', exc_info=True)

        touched = self._upsert_snapshot(records, doors if isinstance(doors, dict) else {})

        # Watermark from the data, not the clock: a record written while this
        # pull was running is then still in range of the next one.
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

    def action_sync_now(self):
        try:
            touched = self._sync_pull()
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
