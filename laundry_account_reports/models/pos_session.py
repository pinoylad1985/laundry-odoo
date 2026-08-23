import logging

from odoo import models
from odoo.tools import float_compare, float_is_zero

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_split_receivable_vals(self, payment, amount, amount_converted):
        """Stamp the originating POS order on split receivable lines.

        Core calls this for split cash, split bank and split pay-later payments
        (see _create_bank_payment_moves, _create_cash_statement_lines_and_cash_move_lines
        and _create_pay_later_receivable_lines). All three land on the session's
        closing entry with a name built from the *session*, so the order is lost.
        Only the pay-later ones stay open long enough to reach Aged Receivable, but
        stamping all of them is free and makes Journal Items traceable too.
        """
        vals = super()._get_split_receivable_vals(payment, amount, amount_converted)
        vals['pos_order_id'] = payment.pos_order_id.id
        return vals

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------

    def _laundry_backfill_pos_order_links(self, batch_size=200):
        """Fill pos_order_id on receivable lines of sessions closed before this module.

        The link was never stored, so it has to be reconstructed. Core names each
        split receivable line '<session name> - <payment method name>', which
        uniquely identifies the payment method, and sets the customer as partner.
        Grouping both sides by (line name, partner) therefore isolates the
        payments of one customer paying with one method in one session; within a
        group, move lines are created in a single `create()` call in the same
        order the payments are iterated, so ids and payments line up.

        Every pair is then checked against the payment amount, and a group whose
        amounts do not agree is left untouched rather than guessed at.

        :return: dict with counts of linked / skipped lines and skipped sessions.
        """
        sessions = self.search(
            [('state', '=', 'closed'), ('move_id', '!=', False)],
            order='id',
        )
        stats = {'sessions': len(sessions), 'linked': 0, 'skipped_lines': 0, 'skipped_sessions': 0}
        pending = []

        for session in sessions:
            payments_by_key = session._laundry_group_split_payments()
            if not payments_by_key:
                continue

            lines_by_key = {}
            for line in session.move_id.line_ids.sorted('id'):
                if line.pos_order_id or not line.partner_id or not line.name:
                    continue
                lines_by_key.setdefault((line.name, line.partner_id.id), []).append(line)

            session_ok = True
            for key, payments in payments_by_key.items():
                lines = lines_by_key.get(key, [])
                if len(lines) != len(payments):
                    # A refund, a manual edit or a partially migrated session:
                    # the two sides no longer correspond one to one.
                    stats['skipped_lines'] += len(lines)
                    session_ok = False
                    continue

                # amount_currency is in the session's currency, the same one
                # pos.payment.amount is expressed in (balance would be the company
                # currency, which only matches in a single-currency database).
                rounding = session.currency_id.rounding
                pairs = list(zip(lines, payments))
                if any(
                    float_compare(line.amount_currency, payment.amount, precision_rounding=rounding) != 0
                    for line, payment in pairs
                ):
                    stats['skipped_lines'] += len(lines)
                    session_ok = False
                    continue

                pending.extend(pairs)
                stats['linked'] += len(pairs)

            if not session_ok:
                stats['skipped_sessions'] += 1
                _logger.warning(
                    "Aged receivable backfill: could not match every receivable line of "
                    "session %s (%s); those lines keep an empty POS order.",
                    session.name, session.id,
                )

            if len(pending) >= batch_size:
                self._laundry_flush_pos_order_links(pending)
                pending = []

        self._laundry_flush_pos_order_links(pending)
        _logger.info("Aged receivable backfill finished: %s", stats)
        return stats

    def _laundry_group_split_payments(self):
        """Replay core's payment iteration and group the pay-later ones by move line key.

        Mirrors _accumulate_amounts: closed orders in order, their payments in
        order, zero amounts skipped, pay-later only, and only for orders that were
        not invoiced (an invoiced order's receivable sits on its invoice instead).
        """
        self.ensure_one()
        rounding = self.currency_id.rounding
        grouped = {}

        for order in self._get_closed_orders():
            if order.is_invoiced:
                continue
            for payment in order.payment_ids:
                if float_is_zero(payment.amount, precision_rounding=rounding):
                    continue
                method = payment.payment_method_id
                if method.type != 'pay_later' or not method.split_transactions:
                    continue
                partner = self.env['res.partner']._find_accounting_partner(payment.partner_id)
                if not partner:
                    continue
                key = ('%s - %s' % (self.name, method.name), partner.id)
                grouped.setdefault(key, []).append(payment)

        return grouped

    def _laundry_flush_pos_order_links(self, pairs):
        """Write the matched pairs, one write() per order rather than per line."""
        if not pairs:
            return

        lines_by_order = {}
        for line, payment in pairs:
            lines_by_order.setdefault(payment.pos_order_id.id, self.env['account.move.line'])
            lines_by_order[payment.pos_order_id.id] |= line

        for order_id, lines in lines_by_order.items():
            lines.write({'pos_order_id': order_id})
