from itertools import chain

from dateutil.relativedelta import relativedelta

from odoo import fields, models
from odoo.tools import SQL

# Header shown for each aging bucket, keyed by expression label. Set here rather
# than left to the inherited _custom_options_initializer, which renames period1..4
# from the (uniform) aging interval of the standard report.
PERIOD_NAMES = {
    'period0': 'Not Due',
    'period1': '1 Day',
    'period2': '2 Days',
    'period3': '3 Days',
    'period4': '4 Days',
    'period5': '5 Days',
    'period6': '6-30',
    'period7': '31-60',
    'period8': '61-120',
    'period9': '121-360',
    'period10': '360+',
}

# Columns filled by _custom_line_postprocessor instead of by the SQL engine.
DOCUMENT_LABELS = ('doc_date', 'doc_ref', 'service_type')


class LaundryAgedReceivableReportHandler(models.AbstractModel):
    _name = 'laundry.aged.receivable.report.handler'
    _inherit = ['account.aged.partner.balance.report.handler']
    _description = 'Laundry Aged Receivable Custom Handler'

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options=previous_options)

        for column in options['columns']:
            if column['expression_label'] in PERIOD_NAMES:
                column['name'] = PERIOD_NAMES[column['expression_label']]

        # The standard filter component offers an aging interval, which is
        # meaningless here: the buckets are fixed and not uniform. Fall back to
        # the plain filter bar, keeping only the line-name template (it carries
        # the Partner and Statement buttons).
        options['custom_display_config'] = {
            'css_custom_class': 'aged_partner_balance',
            'templates': {
                'AccountReportLineName': 'account_reports.AgedPartnerBalanceLineName',
            },
        }
        options['aging_based_on'] = 'base_on_maturity_date'

        # The parent hard-defaults order_column to invoice_date, a column this
        # report does not have, which would leave sort_lines with a column_index
        # of False on PDF/XLSX export. Redo the standard validation (it only keeps
        # a previous choice that matches a sortable column here), then fall back
        # to the document date, oldest first.
        report._init_options_order_column(options, previous_options)
        if not options['order_column']:
            options['order_column'] = {'expression_label': 'doc_date', 'direction': 'ASC'}

    # ------------------------------------------------------------------
    # Aging buckets
    # ------------------------------------------------------------------

    def _laundry_get_aging_periods(self, date_to):
        """Return the aging buckets as (newest date, oldest date) pairs.

        The period table is joined with

            (date_start IS NULL OR aging_date <= date_start)
            AND (date_stop IS NULL OR aging_date >= date_stop)

        so the first element of each tuple is the *newest* date falling in the
        bucket and the second the *oldest*; False means unbounded on that side.
        Order and count must match the period0..periodN columns on the report.
        """
        def minus(days):
            return fields.Date.to_string(date_to - relativedelta(days=days))

        return [
            (False, fields.Date.to_string(date_to)),   # period0   not due yet
            (minus(1), minus(1)),                      # period1   1 day overdue
            (minus(2), minus(2)),                      # period2   2 days
            (minus(3), minus(3)),                      # period3   3 days
            (minus(4), minus(4)),                      # period4   4 days
            (minus(5), minus(5)),                      # period5   5 days
            (minus(6), minus(30)),                     # period6   6-30 days
            (minus(31), minus(60)),                    # period7   31-60 days
            (minus(61), minus(120)),                   # period8   61-120 days
            (minus(121), minus(360)),                  # period9   121-360 days
            (minus(361), False),                       # period10  more than 360 days
        ]

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------

    def _aged_partner_report_custom_engine_common(self, options, internal_type, current_groupby, next_groupby, offset=0, limit=None):
        """Copied from account_reports' AccountAgedPartnerBalanceReportHandler.

        Identical to core except that the period table comes from
        _laundry_get_aging_periods instead of being derived from a uniform aging
        interval, so a diff against core stays readable. Re-check it on upgrade.
        """
        report = self.env['account.report'].browse(options['report_id'])
        report._check_groupby_fields((next_groupby.split(',') if next_groupby else []) + ([current_groupby] if current_groupby else []))

        aging_date_field = SQL.identifier('invoice_date') if options['aging_based_on'] == 'base_on_invoice_date' else SQL.identifier('date_maturity')
        date_to = fields.Date.from_string(options['date']['date_to'])
        periods = self._laundry_get_aging_periods(date_to)

        def build_result_dict(report, query_res_lines):
            rslt = {f'period{i}': 0 for i in range(len(periods))}

            for query_res in query_res_lines:
                for i in range(len(periods)):
                    period_key = f'period{i}'
                    rslt[period_key] += query_res[period_key]

            if current_groupby == 'id':
                query_res = query_res_lines[0]  # We're grouping by id, so there is only 1 element in query_res_lines anyway
                currency = self.env['res.currency'].browse(query_res['currency_id'][0]) if len(query_res['currency_id']) == 1 else None
                rslt.update({
                    'invoice_date': query_res['invoice_date'][0] if len(query_res['invoice_date']) == 1 else None,
                    'due_date': query_res['due_date'][0] if len(query_res['due_date']) == 1 else None,
                    'amount_currency': query_res['amount_currency'],
                    'currency_id': query_res['currency_id'][0] if len(query_res['currency_id']) == 1 else None,
                    'currency': currency.display_name if currency else None,
                    'account_name': query_res['account_name'][0] if len(query_res['account_name']) == 1 else None,
                    'total': None,
                    'has_sublines': True,

                    # Needed by the custom_unfold_all_batch_data_generator, to speed-up unfold_all
                    'partner_id': query_res['partner_id'][0] if query_res['partner_id'] else None,
                })
            else:
                rslt.update({
                    'invoice_date': None,
                    'due_date': None,
                    'amount_currency': None,
                    'currency_id': None,
                    'currency': None,
                    'account_name': None,
                    'total': sum(rslt[f'period{i}'] for i in range(len(periods))),
                    'has_sublines': True,
                })

            return rslt

        # Build period table
        period_table_format = ('(VALUES %s)' % ','.join("(%s, %s, %s)" for period in periods))
        params = list(chain.from_iterable(
            (period[0] or None, period[1] or None, i)
            for i, period in enumerate(periods)
        ))
        period_table = SQL(period_table_format, *params)

        # Build query
        query = report._get_report_query(options, 'strict_range', domain=[('account_id.account_type', '=', internal_type)])
        account_alias = query.left_join(lhs_alias='account_move_line', lhs_column='account_id', rhs_table='account_account', rhs_column='id', link='account_id')
        account_code = self.env['account.account']._field_to_sql(account_alias, 'code', query)

        always_present_groupby = SQL("period_table.period_index")
        if current_groupby:
            groupby_field_sql = self.env['account.move.line']._field_to_sql("account_move_line", current_groupby, query)
            select_from_groupby = SQL("%s AS grouping_key,", groupby_field_sql)
            groupby_clause = SQL("%s, %s", groupby_field_sql, always_present_groupby)
        else:
            select_from_groupby = SQL()
            groupby_clause = always_present_groupby
        multiplicator = -1 if internal_type == 'liability_payable' else 1
        select_period_query = SQL(',').join(
            SQL("""
                CASE WHEN period_table.period_index = %(period_index)s
                THEN %(multiplicator)s * SUM(%(balance_select)s)
                ELSE 0 END AS %(column_name)s
                """,
                period_index=i,
                multiplicator=multiplicator,
                column_name=SQL.identifier(f"period{i}"),
                balance_select=report._currency_table_apply_rate(SQL(
                    "account_move_line.balance - COALESCE(part_debit.amount, 0) + COALESCE(part_credit.amount, 0)"
                )),
            )
            for i in range(len(periods))
        )

        tail_query = report._get_engine_query_tail(offset, limit)
        query = SQL(
            """
            WITH period_table(date_start, date_stop, period_index) AS (%(period_table)s)

            SELECT
                %(select_from_groupby)s
                %(multiplicator)s * (
                    SUM(account_move_line.amount_currency)
                    - COALESCE(SUM(part_debit.debit_amount_currency), 0)
                    + COALESCE(SUM(part_credit.credit_amount_currency), 0)
                ) AS amount_currency,
                ARRAY_AGG(DISTINCT account_move_line.partner_id) AS partner_id,
                ARRAY_AGG(account_move_line.payment_id) AS payment_id,
                ARRAY_AGG(DISTINCT account_move_line.invoice_date) AS invoice_date,
                ARRAY_AGG(DISTINCT COALESCE(account_move_line.%(aging_date_field)s, account_move_line.date)) AS report_date,
                ARRAY_AGG(DISTINCT %(account_code)s) AS account_name,
                ARRAY_AGG(DISTINCT COALESCE(account_move_line.%(aging_date_field)s, account_move_line.date)) AS due_date,
                ARRAY_AGG(DISTINCT account_move_line.currency_id) AS currency_id,
                COUNT(account_move_line.id) AS aml_count,
                ARRAY_AGG(%(account_code)s) AS account_code,
                %(select_period_query)s

            FROM %(table_references)s

            JOIN account_journal journal ON journal.id = account_move_line.journal_id
            %(currency_table_join)s

            LEFT JOIN LATERAL (
                SELECT
                    SUM(part.amount) AS amount,
                    SUM(part.debit_amount_currency) AS debit_amount_currency,
                    part.debit_move_id
                FROM account_partial_reconcile part
                WHERE part.max_date <= %(date_to)s AND part.debit_move_id = account_move_line.id
                GROUP BY part.debit_move_id
            ) part_debit ON TRUE

            LEFT JOIN LATERAL (
                SELECT
                    SUM(part.amount) AS amount,
                    SUM(part.credit_amount_currency) AS credit_amount_currency,
                    part.credit_move_id
                FROM account_partial_reconcile part
                WHERE part.max_date <= %(date_to)s AND part.credit_move_id = account_move_line.id
                GROUP BY part.credit_move_id
            ) part_credit ON TRUE

            JOIN period_table ON
                (
                    period_table.date_start IS NULL
                    OR COALESCE(account_move_line.%(aging_date_field)s, account_move_line.date) <= DATE(period_table.date_start)
                )
                AND
                (
                    period_table.date_stop IS NULL
                    OR COALESCE(account_move_line.%(aging_date_field)s, account_move_line.date) >= DATE(period_table.date_stop)
                )

            WHERE %(search_condition)s

            GROUP BY %(groupby_clause)s

            HAVING
                ROUND(SUM(%(having_debit)s), %(currency_precision)s) != 0
                OR ROUND(SUM(%(having_credit)s), %(currency_precision)s) != 0

            ORDER BY %(groupby_clause)s

            %(tail_query)s
            """,
            account_code=account_code,
            period_table=period_table,
            select_from_groupby=select_from_groupby,
            select_period_query=select_period_query,
            multiplicator=multiplicator,
            aging_date_field=aging_date_field,
            table_references=query.from_clause,
            currency_table_join=report._currency_table_aml_join(options),
            date_to=date_to,
            search_condition=query.where_clause,
            groupby_clause=groupby_clause,
            having_debit=report._currency_table_apply_rate(SQL("CASE WHEN account_move_line.balance > 0  THEN account_move_line.balance else 0 END - COALESCE(part_debit.amount, 0)")),
            having_credit=report._currency_table_apply_rate(SQL("CASE WHEN account_move_line.balance < 0  THEN -account_move_line.balance else 0 END - COALESCE(part_credit.amount, 0)")),
            currency_precision=self.env.company.currency_id.decimal_places,
            tail_query=tail_query,
        )

        self.env.cr.execute(query)
        query_res_lines = self.env.cr.dictfetchall()

        if not current_groupby:
            return build_result_dict(report, query_res_lines)
        else:
            rslt = []

            all_res_per_grouping_key = {}
            for query_res in query_res_lines:
                grouping_key = query_res['grouping_key']
                all_res_per_grouping_key.setdefault(grouping_key, []).append(query_res)

            for grouping_key, query_res_lines in all_res_per_grouping_key.items():
                rslt.append((grouping_key, build_result_dict(report, query_res_lines)))

            return rslt

    # ------------------------------------------------------------------
    # Descriptive columns (Address / Date / Reference / Service Type)
    # ------------------------------------------------------------------

    def _custom_line_postprocessor(self, report, options, lines):
        lines = super()._custom_line_postprocessor(report, options, lines)
        self._laundry_fill_document_columns(report, options, lines)
        self._laundry_fill_partner_address(report, options, lines)
        return lines

    def _laundry_fill_partner_address(self, report, options, lines):
        """Fill the Address column on the customer rows.

        Same no-expression trick as the document columns, one level up: the
        customer rows are res.partner, the rows nested under them are
        account.move.line, so the two fills never touch the same cell.
        """
        column_index = next(
            (
                index
                for index, column in enumerate(options['columns'])
                if column['expression_label'] == 'partner_address'
            ),
            None,
        )
        if column_index is None:
            return

        partner_ids = set()
        for line in lines:
            model, model_id = report._get_model_info_from_id(line['id'])
            if model == 'res.partner' and model_id:
                partner_ids.add(model_id)
        if not partner_ids:
            return

        addresses = {
            partner.id: self._laundry_format_partner_address(partner)
            for partner in self.env['res.partner'].browse(sorted(partner_ids))
        }

        for line in lines:
            model, model_id = report._get_model_info_from_id(line['id'])
            if model != 'res.partner':
                continue

            address = addresses.get(model_id)
            columns = line.get('columns') or []
            if not address or column_index >= len(columns) or not columns[column_index]:
                continue

            columns[column_index].update({'no_format': address, 'is_zero': False})

    def _laundry_format_partner_address(self, partner):
        """One-line address, matching the street/street2 convention used in the POS."""
        address_parts = (
            partner.street,
            partner.street2,
            partner.city,
            # Add partner.state_id.name / partner.zip / partner.country_id.name here
            # if the receivables team needs the full postal address.
        )
        return ", ".join(part for part in address_parts if part)

    def _laundry_fill_document_columns(self, report, options, lines):
        """Fill Date / Reference / Service Type on the receivable-line rows.

        Those three columns deliberately have no account.report.expression: Odoo
        builds one cell per entry in options['columns'], so a column without an
        expression still gets an aligned, blank cell that can be filled here.
        Routing them through the SQL engine instead would mean threading them
        through the unfold-all batch generator too, for values that are per
        document, not per aging bucket.

        _custom_line_postprocessor runs on both the initial render and the unfold
        path, and before _format_column_values, so setting 'no_format' is enough:
        the formatter derives the displayed 'name' from it (a date value is run
        through format_date), and the xlsx export reads 'no_format' directly.
        """
        indexes = {
            column['expression_label']: index
            for index, column in enumerate(options['columns'])
            if column['expression_label'] in DOCUMENT_LABELS
        }
        if not indexes:
            return

        line_ids = set()
        for line in lines:
            model, model_id = report._get_model_info_from_id(line['id'])
            if model == 'account.move.line' and model_id:
                line_ids.add(model_id)
        if not line_ids:
            return

        documents = self._laundry_get_document_values(line_ids)

        for line in lines:
            model, model_id = report._get_model_info_from_id(line['id'])
            if model != 'account.move.line':
                continue

            values = documents.get(model_id)
            if not values:
                continue

            columns = line.get('columns') or []
            for label, index in indexes.items():
                value = values.get(label)
                if value is None or index >= len(columns) or not columns[index]:
                    continue
                columns[index].update({'no_format': value, 'is_zero': False})

    def _laundry_get_document_values(self, line_ids):
        """Resolve each open receivable line to the document it came from.

        Three shapes reach this report:

        * a POS order paid on Customer Account -- the receivable line sits on the
          *session's* closing entry and is named after the session, so the only
          thing tying it to an order is account.move.line.pos_order_id, which we
          stamp in pos_session.py (run _laundry_backfill_pos_order_links() once
          for orders created before that);
        * a POS order that was invoiced -- pos.order.account_move points at the
          invoice, so account.move.pos_order_ids resolves it and the row is
          reported at invoice level;
        * anything invoiced outside the POS (Sales, manual) -- no order at all, so
          the row stays at invoice level with a blank service type.
        """
        # sudo: accountants do not necessarily have read access on pos.order.
        move_lines = self.env['account.move.line'].browse(sorted(line_ids)).sudo()

        service_labels = {}
        pos_order = self.env['pos.order']
        if 'laundry_service_type' in pos_order._fields:
            selection = pos_order.fields_get(['laundry_service_type'])['laundry_service_type']['selection']
            service_labels = dict(selection)

        values = {}
        for move_line in move_lines:
            order = move_line.pos_order_id or move_line.move_id.pos_order_ids[:1]
            move = move_line.move_id
            if order:
                values[move_line.id] = {
                    # date_order is a UTC datetime; show it in the user's timezone
                    # so an evening order does not land on the next day.
                    'doc_date': (
                        fields.Datetime.context_timestamp(order, order.date_order).date()
                        if order.date_order else move_line.date
                    ),
                    'doc_ref': order.name,
                    'service_type': service_labels.get(order.laundry_service_type, ''),
                }
            else:
                values[move_line.id] = {
                    'doc_date': move.invoice_date or move_line.date,
                    'doc_ref': move.name,
                    'service_type': '',
                }

        return values

    # ------------------------------------------------------------------
    # Drill-down
    # ------------------------------------------------------------------

    def open_journal_items(self, options, params):
        """Same receivable pre-filter the standard Aged Receivable applies.

        Inherited from the *partner balance* handler rather than the receivable
        one, so this (and action_audit_cell below) has to be restated here.
        """
        receivable_account_type = {'id': 'trade_receivable', 'name': 'Receivable', 'selected': True}

        if 'account_type' in options:
            options['account_type'].append(receivable_account_type)
        else:
            options['account_type'] = [receivable_account_type]

        return super().open_journal_items(options, params)

    def action_audit_cell(self, options, params):
        return self.aged_partner_balance_audit(options, params, 'sale')

    def _build_domain_from_period(self, options, period):
        """Domain behind one aging cell, matching _laundry_get_aging_periods.

        Fully replaces the standard version, which assumes six uniform 30-day
        buckets and reads the index off the last character (so 'period10' would
        be parsed as period 0).
        """
        if not period.startswith('period') or not period[len('period'):].isdigit():
            return []

        index = int(period[len('period'):])
        periods = self._laundry_get_aging_periods(fields.Date.from_string(options['date']['date_to']))
        if index >= len(periods):
            return []

        newest, oldest = periods[index]
        bounds = []
        if newest:
            bounds.append(('<=', newest))
        if oldest:
            bounds.append(('>=', oldest))

        def all_of(leaves):
            return ['&'] * (len(leaves) - 1) + leaves

        # Mirrors the COALESCE(date_maturity, date) the engine ages on.
        on_maturity = all_of([('date_maturity', operator, value) for operator, value in bounds])
        on_entry_date = all_of(
            [('date_maturity', '=', False)] + [('date', operator, value) for operator, value in bounds]
        )
        return ['|'] + on_maturity + on_entry_date
