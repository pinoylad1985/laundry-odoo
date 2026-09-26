"""Put a drop-off's pickup and delivery on Google Tasks, to be ticked off.

A task and not a calendar event, because these are things to DO: they want a
checkbox, and they want to stay in front of somebody until it is ticked. Google
Tasks show up in Google Calendar too, in the day's Tasks row, so they are read
in the same place an event would have been.

The cost of that, and it is worth knowing: TASKS CANNOT BE SHARED. Google has
no sharing model for them at all - no equivalent of sharing a calendar with
"make changes to events". The tasks written here land in ONE Google account's
own list, and only that account sees them. Staff and drivers cannot be given
them. If they ever need the schedule, that is a shared calendar, which is a
different thing that would sit alongside this one.

That is also why this authenticates as a USER and not as a service account. A
service account has its own private task list that no human can open, and the
only way to make it write into a person's list is domain-wide delegation,
which needs Google Workspace. A one-time browser sign-in, kept as a refresh
token, puts the tasks in a real person's real list instead.

No new dependency: the refresh grant is one form POST and the Tasks API is
JSON over HTTPS, both of which urllib does - the same way the Firebase sync in
this module already talks to the world.

Setup, once, in Settings > Technical > System Parameters:
  laundry_locker.google_oauth_client_id       from the OAuth client
  laundry_locker.google_oauth_client_secret   from the same client
  laundry_locker.google_oauth_refresh_token   from the one-time sign-in
  laundry_locker.google_tasklist_id           optional; blank = "My Tasks"

The link back to the task lives on the locker row, so a second press edits the
task instead of adding another. That means it is lost if the locker list is
ever deleted and rebuilt from the feed, and the tasks already in Google are
then orphaned: pressing again makes fresh ones, and the old ones have to be
deleted by hand. Rebuilding the list is a rare, deliberate act, and the
alternative - searching the task list by title on every press - buys that one
case at the cost of a second round trip every time.
"""

import json
import logging
import re
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .laundry_locker_sync import FEED_TIMEZONE, REQUEST_TIMEOUT

_logger = logging.getLogger(__name__)

PARAM_CLIENT_ID = 'laundry_locker.google_oauth_client_id'
PARAM_CLIENT_SECRET = 'laundry_locker.google_oauth_client_secret'
PARAM_REFRESH_TOKEN = 'laundry_locker.google_oauth_refresh_token'
PARAM_TASKLIST = 'laundry_locker.google_tasklist_id'

TOKEN_URL = 'https://oauth2.googleapis.com/token'
TASKS_BASE = 'https://tasks.googleapis.com/tasks/v1/lists'

# The signed-in account's own "My Tasks". Google accepts this in place of a
# list id, which spares the setup a step: a dedicated list is nicer, but its id
# has to be fetched through the API and no screen in Google shows it.
DEFAULT_TASKLIST = '@default'

# What Google's own Task dialog defaults a task to, and what the drop-off time
# is therefore taken to be the END of: the picked time is when the customer is
# promised the bag, so the half hour runs UP TO it. A task starting at the
# promised minute would read as though there were half an hour left after it,
# which is the opposite of what is true.
TASK_MINUTES = 30

# How many digits of the phone and characters of the ref the title carries.
# Five of each: the ref's random part is exactly five characters after the LX-
# prefix, and five digits of a phone number separate two customers in a day's
# list without putting a whole number on the screen.
TITLE_DIGITS = 5

# Stands in for a half of the title that is not there, so every title keeps the
# same 5-5 shape and a missing phone is visible rather than a title that has
# quietly shifted along.
TITLE_MISSING = '?????'

# Which datetime each button means, what the task is called, and the two
# letters its title ends with. Keyed by the `kind` the buttons pass, so the two
# paths differ in this table and nowhere else.
TASK_KINDS = {
    'pickup': {
        'field': 'pickup_datetime',
        'task_field': 'google_pickup_task_id',
        'label': 'Pickup',
        'suffix': 'PU',
    },
    'delivery': {
        'field': 'delivery_datetime',
        'task_field': 'google_delivery_task_id',
        'label': 'Delivery',
        'suffix': 'DL',
    },
}


class LaundryLockerTransaction(models.Model):
    _inherit = 'laundry.locker.transaction'

    # Kept so a second press EDITS the task instead of putting another copy of
    # it in the list. copy=False: a duplicated row is not the same booking, and
    # must not claim the original's task.
    google_pickup_task_id = fields.Char(
        string='Pickup Task', copy=False, readonly=True,
        help="The Google task this drop-off's pickup was put on. Set when the "
             'task is created; pressing the button again updates that task '
             'rather than creating a second one.',
    )
    google_delivery_task_id = fields.Char(
        string='Delivery Task', copy=False, readonly=True,
        help="The Google task this drop-off's delivery was put on. Set when "
             'the task is created; pressing the button again updates that task '
             'rather than creating a second one.',
    )

    # A column the counter can run an eye down, rather than two task ids that
    # only say yes by being long. It reads 'PU DL' when both have been made,
    # one of them when only one has, and is empty when neither has - so a row
    # that still needs doing is blank, which is what a list of things to do
    # should look like.
    #
    # Not stored: it says nothing the two ids do not already say, and a stored
    # copy is one more thing that can come to disagree with them.
    google_task_set = fields.Char(
        string='Calendar', compute='_compute_google_task_set',
        help="Which of this drop-off's times have been put on Google Tasks: "
             'PU for the pickup, DL for the delivery. Empty means neither has '
             'been yet.',
    )

    @api.depends('google_pickup_task_id', 'google_delivery_task_id')
    def _compute_google_task_set(self):
        for transaction in self:
            marks = [
                spec['suffix'] for spec in TASK_KINDS.values()
                if transaction[spec['task_field']]
            ]
            # False and not '' so the cell is genuinely empty rather than an
            # empty badge sitting there looking like a label that lost its text.
            transaction.google_task_set = ' '.join(marks) or False

    # ------------------------------------------------------------------
    # signing in
    # ------------------------------------------------------------------
    def _google_oauth_config(self):
        """The three halves of the sign-in, or a UserError naming what is gone.

        Every failure here is a setup that was never finished, so each one says
        which parameter to go and fill in. Returning False would look instead
        like Google having refused the task.
        """
        params = self.env['ir.config_parameter'].sudo()
        config = {}
        for key, param in (
            ('client_id', PARAM_CLIENT_ID),
            ('client_secret', PARAM_CLIENT_SECRET),
            ('refresh_token', PARAM_REFRESH_TOKEN),
        ):
            config[key] = (params.get_param(param) or '').strip()
            if not config[key]:
                raise UserError(_(
                    'Google Tasks is not set up yet: the system parameter %s '
                    'is empty.', param,
                ))
        config['tasklist'] = (
            (params.get_param(PARAM_TASKLIST) or '').strip() or DEFAULT_TASKLIST
        )
        return config

    def _google_access_token(self, config):
        """Spend the refresh token on an access token.

        Fetched per press rather than cached. A counter puts one drop-off on
        the list at a time, so the extra round trip costs a moment of a button
        press, and a cached token is one more thing that can be stale when
        something is already going wrong.
        """
        body = urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': config['refresh_token'],
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
        }).encode('ascii')
        request = Request(
            TOKEN_URL, data=body, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode('utf-8'))['access_token']
        except HTTPError as exc:
            detail = self._google_error_detail(exc)
            if 'invalid_grant' in detail:
                # Named explicitly, because the cause is invisible and the
                # symptom is a setup that worked for a week and then stopped:
                # Google revokes refresh tokens after 7 days while the OAuth
                # consent screen is still in Testing.
                raise UserError(_(
                    'Google has revoked the stored sign-in.\n\n%(detail)s\n\n'
                    'If this worked and then stopped after about a week, the '
                    'OAuth consent screen is still in Testing - set its '
                    'publishing status to In production, sign in again, and '
                    'put the new refresh token in %(param)s.',
                    detail=detail, param=PARAM_REFRESH_TOKEN,
                )) from exc
            raise UserError(_(
                'Google refused the sign-in (HTTP %(code)s).\n\n%(detail)s\n\n'
                'Check the client id and secret, and that the Google Tasks API '
                'is enabled on the project.',
                code=exc.code, detail=detail,
            )) from exc
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                'Could not reach Google to sign in: %s', exc,
            )) from exc

    # ------------------------------------------------------------------
    # the task itself
    # ------------------------------------------------------------------
    @staticmethod
    def _google_error_detail(exc):
        """Google's own words for a refusal, which are usually the useful bit.

        Two shapes, because two endpoints: the Tasks API nests its message
        under `error`, while the token endpoint answers with a flat
        error/error_description pair. Falls back to the raw body - a message we
        cannot parse is still worth more to whoever is fixing the setup than
        'request failed'.
        """
        try:
            body = exc.read().decode('utf-8')
        except Exception:  # noqa: BLE001
            return ''
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            return body[:400]
        if isinstance(payload.get('error'), dict):
            return payload['error'].get('message') or body[:400]
        return ' '.join(filter(None, (
            payload.get('error'), payload.get('error_description'),
        ))) or body[:400]

    def _google_tasks_request(self, method, token, config, task_id=None, payload=None):
        # safe='@' keeps the @default sentinel readable in the URL; Google
        # accepts it unescaped and escaping it would not match the list.
        url = '%s/%s/tasks' % (TASKS_BASE, quote(config['tasklist'], safe='@'))
        if task_id:
            url = '%s/%s' % (url, quote(task_id, safe=''))
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

    def _google_task_title(self, kind):
        """`34567-9nXDX PU` - five of the phone, five of the ref, then which.

        A task list is read at a glance, on a phone, which gives a title maybe
        twenty characters before it truncates. So the title is only what tells
        two drop-offs apart, in a fixed shape that can be scanned down a
        column: the customer's number, the locker's ref, and PU or DL.
        Everything else is in the notes, one tap away.

        Five digits and not the whole number because the number is not what
        identifies the job, and a full one does not need to be on the screen to
        say which of today's bags this is. Five of the ref because the random
        part of an LX- ref is exactly that long.

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
            TASK_KINDS[kind]['suffix'],
        )

    @staticmethod
    def _google_stamp(local):
        """`Sat 26 Sep 2026, 2:00 PM` - built by hand, not by strftime.

        %-I (an hour with no leading zero) is a GNU extension and would be a
        trap the day this runs anywhere else, and plain %I leaves '02:00 PM',
        which reads for a moment like a 24-hour clock.
        """
        return '%s, %d:%02d %s' % (
            local.strftime('%a %d %b %Y'),
            (local.hour % 12) or 12, local.minute,
            'AM' if local.hour < 12 else 'PM',
        )

    def _google_task_body(self, kind):
        """The task: a terse title, the promised time, then every detail.

        `due` is sent as the full instant, with its time, because Google's own
        Task dialog takes a time and blocks out half an hour from it. It is set
        to half an hour BEFORE the promised moment, so that the block ENDS when
        the customer is promised the bag - the same reading as everywhere else
        here: the picked time is the deadline, and the work goes in front of it.

        Sent in UTC, which is what RFC 3339 with a Z means, and which is also
        how it lands on the right DAY: an instant is unambiguous where a bare
        date is not, and a date taken from the UTC clock instead of the shop's
        would fall on the previous day for anything promised before 8am here.

        THE TIME IS ALSO THE FIRST LINE OF THE NOTES, and that is not belt and
        braces - it is the fallback. The Tasks API has long documented `due` as
        date-only, with the time discarded, even though the UI now keeps one.
        If Google truncates it, the notes still say the promised minute and
        nothing is lost but the placement in the day.

        The notes are the same for both kinds, because whoever opens one wants
        the whole drop-off in front of them, not the half of it that matches the
        button that happened to be pressed. The location is among them for the
        same reason it would have been an event's `location`: a task has no
        field of its own for one.
        """
        self.ensure_one()
        spec = TASK_KINDS[kind]
        when = self[spec['field']]
        local = pytz.utc.localize(when).astimezone(pytz.timezone(FEED_TIMEZONE))
        starts = when - timedelta(minutes=TASK_MINUTES)

        lines = [
            _('%(label)s: %(when)s',
              label=spec['label'], when=self._google_stamp(local)),
            _('Ref: %s', self.ref or '-'),
            _('Customer: %s',
              self.customer_name or self.partner_id.display_name or _('Unnamed')),
        ]
        if self.phone:
            lines.append(_('Phone: %s', self.phone))
        if self.location_name:
            lines.append(_('Location: %s', self.location_name))
        if self.dirty_door:
            lines.append(_('Door: %s', self.dirty_door))
        if self.service:
            lines.append(_('Service: %s', self.service))
        if self.turnaround:
            lines.append(_('Turnaround: %s', self.turnaround))
        if self.status_label:
            lines.append(_('Status: %s', self.status_label))

        return {
            # Not translated: it is an identifier read off a screen, and the
            # two letters mean the same in every language the shop speaks.
            'title': self._google_task_title(kind),
            'notes': '\n'.join(lines),
            'due': starts.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        }

    def _google_push_task(self, kind):
        """Make the task, or edit the one this row already made.

        Deleting the task in Google and pressing again has to work: Google
        answers 404 for a task that is gone and 410 for one deleted a while
        back, and in both cases the id being held is worthless, so it is
        dropped and a fresh task made. Anything else is a real failure and is
        raised - somebody pressed a button and is entitled to know it did not
        happen.
        """
        self.ensure_one()
        spec = TASK_KINDS[kind]
        if not self[spec['field']]:
            raise UserError(_(
                'This drop-off has no %s time, so there is nothing to put on '
                'the list.', spec['label'].lower(),
            ))

        config = self._google_oauth_config()
        token = self._google_access_token(config)
        payload = self._google_task_body(kind)
        task_id = self[spec['task_field']]

        try:
            if task_id:
                task = self._google_tasks_request(
                    'PATCH', token, config, task_id=task_id, payload=payload)
            else:
                task = self._google_tasks_request(
                    'POST', token, config, payload=payload)
        except HTTPError as exc:
            if task_id and exc.code in (404, 410):
                _logger.info(
                    'Locker %s: its %s task is gone from Google; making a new '
                    'one.', self.ref, kind,
                )
                task = self._google_tasks_request(
                    'POST', token, config, payload=payload)
            elif exc.code == 404:
                raise UserError(_(
                    'That account has no task list "%(list)s".\n\nLeave '
                    '%(param)s empty to use My Tasks.',
                    list=config['tasklist'], param=PARAM_TASKLIST,
                )) from exc
            elif exc.code in (401, 403):
                raise UserError(_(
                    'Google will not let this sign-in write tasks (HTTP '
                    '%(code)s).\n\n%(detail)s\n\nCheck that the Google Tasks '
                    'API is enabled on the project and that the sign-in was '
                    'granted the tasks scope.',
                    code=exc.code, detail=self._google_error_detail(exc),
                )) from exc
            else:
                raise UserError(_(
                    'Google refused the task (HTTP %(code)s).\n\n%(detail)s',
                    code=exc.code, detail=self._google_error_detail(exc),
                )) from exc
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                'Could not reach Google Tasks: %s', exc,
            )) from exc

        self[spec['task_field']] = task.get('id') or False
        return task

    # ------------------------------------------------------------------
    # buttons
    # ------------------------------------------------------------------
    def _google_task_button(self, kind):
        """Put every selected row on the list, then say what happened.

        Rows are done one at a time and the first failure stops the lot, so
        nothing is half-written without somebody being told; Odoo rolls the
        request back, which also clears the task ids just stored. Google will
        then be holding tasks nothing points at - harmless, and better than
        pretending a batch succeeded.
        """
        for transaction in self:
            transaction._google_push_task(kind)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _(
                    '%(count)s %(label)s added to Google Tasks.',
                    count=len(self), label=TASK_KINDS[kind]['label'].lower(),
                ),
            },
        }

    def action_google_task_pickup(self):
        return self._google_task_button('pickup')

    def action_google_task_delivery(self):
        return self._google_task_button('delivery')
