"""Put a drop-off's pickup and delivery on the shop's Google calendar.

One shared calendar, written server-side, so the schedule is the same for
everyone who opens it and nobody has to remember to press Save in their own
Google account.

Authentication is a SERVICE ACCOUNT, not the OAuth consent flow that Odoo's
own `google_calendar` module uses. That module syncs each user's personal
calendar and needs every user to authorise it; here there is one calendar
belonging to the shop, written by the server, with no user in the picture at
all.

The Odoo image has neither `google-auth` nor `googleapiclient`, so the token
is minted by hand: a service-account grant is an RS256 JWT, and `cryptography`
(which the image does have) signs one in a few lines. That keeps this a module
change rather than a rebuild of the image.

The link back to the event lives on the locker row, so a second press edits
the event instead of adding another. That means it is lost if the locker list
is ever deleted and rebuilt from the feed, and the events already on the
calendar will then be orphaned: pressing again makes fresh ones, and the old
ones have to be deleted in Google. Rebuilding the list is a rare, deliberate
act, and the alternative - searching the calendar by ref on every press - buys
that one case at the cost of a second round trip every time.

Setup, once, in Settings > Technical > System Parameters:
  laundry_locker.google_calendar_id       the calendar to write into
  laundry_locker.google_service_account   the service-account JSON, whole
The calendar must be shared with the service account's own address, with
"Make changes to events".
"""

import base64
import json
import logging
import re
import time
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytz
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from odoo import _, fields, models
from odoo.exceptions import UserError

from .laundry_locker_sync import FEED_TIMEZONE, REQUEST_TIMEOUT

_logger = logging.getLogger(__name__)

PARAM_CALENDAR_ID = 'laundry_locker.google_calendar_id'
PARAM_SERVICE_ACCOUNT = 'laundry_locker.google_service_account'

TOKEN_URL = 'https://oauth2.googleapis.com/token'
CALENDAR_BASE = 'https://www.googleapis.com/calendar/v3/calendars'
JWT_GRANT = 'urn:ietf:params:oauth:grant-type:jwt-bearer'

# Only events, and only on calendars already shared with the service account.
# Not `/auth/calendar`, which would also let it create and delete calendars.
SCOPE = 'https://www.googleapis.com/auth/calendar.events'

# Google caps a service-account assertion at an hour.
TOKEN_LIFETIME = 3600

# The feed gives a moment, not a span, and that moment is the DEADLINE - when
# the customer is promised the bag. So the event runs for the half hour
# BEFORE it: long enough to read as an appointment in a day view rather than
# a hairline at the top of the hour, and placed where the work is.
EVENT_MINUTES = 30

# How many digits of the phone and characters of the ref the title carries.
# Five of each: the ref's random part is exactly five characters after the LX-
# prefix, and five digits of a phone number separate two customers on a day's
# calendar without putting a whole number on a shared screen.
TITLE_DIGITS = 5

# Stands in for a half of the title that is not there, so every title keeps the
# same 5-5 shape and a missing phone is visible rather than a title that has
# quietly shifted along.
TITLE_MISSING = '?????'

# Which datetime each button means, what the event is called, and the two
# letters its title ends with. Keyed by the `kind` the buttons pass, so the two
# paths differ in this table and nowhere else.
EVENT_KINDS = {
    'pickup': {
        'field': 'pickup_datetime',
        'event_field': 'google_pickup_event_id',
        'label': 'Pickup',
        'suffix': 'PU',
    },
    'delivery': {
        'field': 'delivery_datetime',
        'event_field': 'google_delivery_event_id',
        'label': 'Delivery',
        'suffix': 'DL',
    },
}


class LaundryLockerTransaction(models.Model):
    _inherit = 'laundry.locker.transaction'

    # Kept so a second press EDITS the event instead of laying another copy of
    # it on the calendar. copy=False: a duplicated row is not the same booking,
    # and must not claim the original's event.
    google_pickup_event_id = fields.Char(
        string='Pickup Event', copy=False, readonly=True,
        help='The Google Calendar event this drop-off\'s pickup was put on. '
             'Set when the event is created; pressing the button again '
             'updates that event rather than creating a second one.',
    )
    google_delivery_event_id = fields.Char(
        string='Delivery Event', copy=False, readonly=True,
        help='The Google Calendar event this drop-off\'s delivery was put on. '
             'Set when the event is created; pressing the button again '
             'updates that event rather than creating a second one.',
    )

    # ------------------------------------------------------------------
    # credentials
    # ------------------------------------------------------------------
    def _google_service_account(self):
        """The service-account JSON, or a UserError naming what is missing.

        Every failure here is a setup that was never done, so each one says
        which parameter to go and fill in. A button that just returned False
        would look like the calendar had quietly refused the event.
        """
        params = self.env['ir.config_parameter'].sudo()
        raw = (params.get_param(PARAM_SERVICE_ACCOUNT) or '').strip()
        if not raw:
            raise UserError(_(
                'No Google service account is configured.\n\n'
                'Paste the service-account JSON into the system parameter '
                '%s.', PARAM_SERVICE_ACCOUNT,
            ))
        try:
            info = json.loads(raw)
        except ValueError as exc:
            raise UserError(_(
                'The Google service account in %s is not valid JSON.\n\n'
                'Paste the downloaded key file whole, braces included.',
                PARAM_SERVICE_ACCOUNT,
            )) from exc
        for key in ('client_email', 'private_key'):
            if not info.get(key):
                raise UserError(_(
                    'The Google service account in %(param)s has no '
                    '"%(key)s". That is not a service-account key file - '
                    'an OAuth client secret looks similar but will not work '
                    'here.', param=PARAM_SERVICE_ACCOUNT, key=key,
                ))
        return info

    def _google_calendar_id(self):
        calendar_id = (self.env['ir.config_parameter'].sudo()
                       .get_param(PARAM_CALENDAR_ID) or '').strip()
        if not calendar_id:
            raise UserError(_(
                'No Google calendar is configured.\n\n'
                'Put the calendar\'s ID in the system parameter %s. It is on '
                'the calendar\'s own settings page in Google Calendar, under '
                '"Integrate calendar".', PARAM_CALENDAR_ID,
            ))
        return calendar_id

    @staticmethod
    def _b64url(raw):
        """base64url with the padding stripped, which is what JWT wants."""
        return base64.urlsafe_b64encode(raw).rstrip(b'=')

    def _google_access_token(self):
        """Trade a self-signed JWT for an access token.

        Fetched per press rather than cached. A cashier puts one drop-off on
        the calendar at a time, so the extra round trip costs a moment of a
        button press, and a cached token is one more thing that can be stale
        when something is already going wrong.
        """
        info = self._google_service_account()
        now = int(time.time())
        claims = {
            'iss': info['client_email'],
            'scope': SCOPE,
            'aud': TOKEN_URL,
            'iat': now,
            'exp': now + TOKEN_LIFETIME,
        }
        segments = [
            self._b64url(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode()),
            self._b64url(json.dumps(claims).encode()),
        ]
        signing_input = b'.'.join(segments)

        try:
            key = serialization.load_pem_private_key(
                info['private_key'].encode('utf-8'), password=None,
            )
            signature = key.sign(
                signing_input, padding.PKCS1v15(), hashes.SHA256(),
            )
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                'Could not sign in as the Google service account - its '
                'private key was not readable.\n\n%s', exc,
            )) from exc

        assertion = b'.'.join([signing_input, self._b64url(signature)])
        body = ('grant_type=%s&assertion=%s' % (
            quote(JWT_GRANT, safe=''), assertion.decode('ascii'),
        )).encode('ascii')
        request = Request(
            TOKEN_URL, data=body, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                token = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            raise UserError(_(
                'Google refused the service account (HTTP %(code)s).\n\n'
                '%(detail)s\n\nCheck that the Calendar API is enabled on the '
                'project and that the key has not been revoked.',
                code=exc.code, detail=self._google_error_detail(exc),
            )) from exc
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                'Could not reach Google to sign in: %s', exc,
            )) from exc
        return token['access_token']

    # ------------------------------------------------------------------
    # the calendar itself
    # ------------------------------------------------------------------
    @staticmethod
    def _google_error_detail(exc):
        """Google's own words for a refusal, which are usually the useful bit.

        Falls back to the raw body: a message we cannot parse is still worth
        more to whoever is fixing the setup than 'request failed'.
        """
        try:
            body = exc.read().decode('utf-8')
        except Exception:  # noqa: BLE001
            return ''
        try:
            return json.loads(body)['error']['message']
        except Exception:  # noqa: BLE001
            return body[:400]

    def _google_calendar_request(self, method, token, event_id=None, payload=None):
        calendar_id = self._google_calendar_id()
        url = '%s/%s/events' % (CALENDAR_BASE, quote(calendar_id, safe=''))
        if event_id:
            url = '%s/%s' % (url, quote(event_id, safe=''))
        request = Request(
            url, method=method,
            data=json.dumps(payload).encode('utf-8') if payload else None,
            headers={
                'Authorization': 'Bearer %s' % token,
                'Content-Type': 'application/json',
            },
        )
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode('utf-8'))

    def _google_event_title(self, kind):
        """`34567-9nXDX PU` - five of the phone, five of the ref, then which.

        A shared calendar is read at a glance, on a phone, where a day view
        gives a title perhaps twenty characters before it truncates. So the
        title is only what tells two drop-offs apart, in a fixed shape that can
        be scanned down a column: the customer's number, the locker's ref, and
        PU or DL. Everything a driver actually needs is in the description, one
        tap away.

        Five digits and not the whole number because this calendar is shared
        with whoever is driving, and a full phone number does not need to be on
        it to say which of today's bags this is. Five of the ref because the
        random part of an LX- ref is exactly that long.

        The digits come from phone_last10 where there is one - that is the same
        normalised form the search box matches, so a title and a search agree
        about what a number is - and from the raw phone stripped to its digits
        otherwise.
        """
        self.ensure_one()
        digits = self.phone_last10 or re.sub(r'\D', '', self.phone or '')
        ref = (self.ref or '').strip()
        return '%s-%s %s' % (
            digits[-TITLE_DIGITS:] or TITLE_MISSING,
            ref[-TITLE_DIGITS:] or TITLE_MISSING,
            EVENT_KINDS[kind]['suffix'],
        )


    def _google_event_body(self, kind):
        """What the event says: a terse title, and every detail beneath it.

        The description is the same for both events, because whoever opens
        one wants the whole drop-off in front of them, not the half of it
        that matches the button that happened to be pressed.
        """
        self.ensure_one()
        spec = EVENT_KINDS[kind]
        when = self[spec['field']]
        local = pytz.utc.localize(when).astimezone(pytz.timezone(FEED_TIMEZONE))

        who = self.customer_name or self.partner_id.display_name or _('Unnamed')
        lines = [
            _('Ref: %s', self.ref or '-'),
            _('Customer: %s', who),
        ]
        if self.phone:
            lines.append(_('Phone: %s', self.phone))
        if self.dirty_door:
            lines.append(_('Door: %s', self.dirty_door))
        if self.service:
            lines.append(_('Service: %s', self.service))
        if self.turnaround:
            lines.append(_('Turnaround: %s', self.turnaround))
        if self.status_label:
            lines.append(_('Status: %s', self.status_label))

        return {
            # Not translated: it is an identifier read off a screen, and
            # the two letters mean the same in every language the shop
            # speaks.
            'summary': self._google_event_title(kind),
            'description': '\n'.join(lines),
            'location': self.location_name or '',
            # The picked time is when the customer is promised the bag, so
            # it is the END of the half hour: the block sits in front of
            # the deadline, which is the time that has to be worked back
            # from. An event starting at the promised minute would say the
            # opposite - that there is half an hour left after it.
            'start': {
                'dateTime': (local - timedelta(minutes=EVENT_MINUTES)).isoformat(),
                'timeZone': FEED_TIMEZONE,
            },
            'end': {
                'dateTime': local.isoformat(),
                'timeZone': FEED_TIMEZONE,
            },
        }

    def _google_put_on_calendar(self, kind):
        """Create the event, or edit the one this row already made.

        Deleting the event in Google and pressing again has to work: Google
        answers 410 for an event that was deleted and 404 for one that is gone
        altogether, and in both cases the id we are holding is worthless, so
        it is dropped and a fresh event created. Anything else is a real
        failure and is raised - the cashier pressed a button and is entitled
        to know it did not happen.
        """
        self.ensure_one()
        spec = EVENT_KINDS[kind]
        if not self[spec['field']]:
            raise UserError(_(
                'This drop-off has no %s time, so there is nothing to put on '
                'the calendar.', spec['label'].lower(),
            ))

        token = self._google_access_token()
        payload = self._google_event_body(kind)
        event_id = self[spec['event_field']]

        try:
            if event_id:
                event = self._google_calendar_request(
                    'PATCH', token, event_id=event_id, payload=payload)
            else:
                event = self._google_calendar_request(
                    'POST', token, payload=payload)
        except HTTPError as exc:
            if event_id and exc.code in (404, 410):
                _logger.info(
                    'Locker %s: its %s event is gone from Google; making a '
                    'new one.', self.ref, kind,
                )
                event = self._google_calendar_request(
                    'POST', token, payload=payload)
            elif exc.code == 403:
                raise UserError(_(
                    'Google will not let the service account write to that '
                    'calendar.\n\n%(detail)s\n\nShare the calendar with '
                    '%(who)s and give it "Make changes to events".',
                    detail=self._google_error_detail(exc),
                    who=self._google_service_account()['client_email'],
                )) from exc
            elif exc.code == 404:
                raise UserError(_(
                    'Google has no calendar with that ID.\n\nCheck %s.',
                    PARAM_CALENDAR_ID,
                )) from exc
            else:
                raise UserError(_(
                    'Google refused the event (HTTP %(code)s).\n\n%(detail)s',
                    code=exc.code, detail=self._google_error_detail(exc),
                )) from exc
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                'Could not reach Google Calendar: %s', exc,
            )) from exc

        self[spec['event_field']] = event.get('id') or False
        return event

    # ------------------------------------------------------------------
    # buttons
    # ------------------------------------------------------------------
    def _google_calendar_button(self, kind):
        """Put every selected row on the calendar, then say what happened.

        Rows are done one at a time and the first failure stops the lot, so
        nothing is half-written without the cashier being told; Odoo rolls the
        request back, which also clears the event ids just stored. Google will
        then be holding events nothing points at - harmless, and better than
        pretending a batch succeeded.
        """
        label = EVENT_KINDS[kind]['label']
        for transaction in self:
            transaction._google_put_on_calendar(kind)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _(
                    '%(count)s %(label)s put on the shop calendar.',
                    count=len(self), label=label.lower(),
                ),
            },
        }

    def action_google_calendar_pickup(self):
        return self._google_calendar_button('pickup')

    def action_google_calendar_delivery(self):
        return self._google_calendar_button('delivery')
