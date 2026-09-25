import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# The locker services run on the SAME VPS as Odoo, so this push is a localhost
# POST - no Firebase read, no egress, and the transaction is in Odoo the moment
# the locker records it rather than up to an hour later.
TOKEN_HEADER = 'X-Laundry-Token'


class LaundryLockerController(http.Controller):

    @http.route(
        '/laundry_locker/push', type='http', auth='public', methods=['POST'],
        csrf=False, save_session=False,
    )
    def laundry_locker_push(self, **kwargs):
        """Accept one or more snapshot records from the locker sync service.

        Body: a single record, or `{"records": [...]}`, in the same shape the
        service writes to /lockerTransactions - so the mapping is shared with
        the pull and the two paths cannot disagree.
        """
        model = request.env['laundry.locker.transaction'].sudo()
        token = request.httprequest.headers.get(TOKEN_HEADER, '')
        if not model._check_push_token(token):
            # Includes the case of no token configured at all: an unset secret
            # closes the endpoint, it does not open it.
            return request.make_json_response({'error': 'unauthorized'}, status=401)

        raw = request.httprequest.get_data(as_text=True) or ''
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            return request.make_json_response({'error': 'invalid_json'}, status=400)

        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            records = payload.get('records')
            if records is None:
                records = [payload] if payload.get('ref') else []
        else:
            records = []
        if not records:
            return request.make_json_response({'error': 'no_records'}, status=400)

        try:
            touched = model._upsert_snapshot(records)
        except Exception:  # noqa: BLE001 - never hand a traceback to a caller
            _logger.exception('Locker push failed')
            return request.make_json_response({'error': 'server_error'}, status=500)

        return request.make_json_response({
            'ok': True,
            'count': len(touched),
            'refs': touched.mapped('ref'),
        })
