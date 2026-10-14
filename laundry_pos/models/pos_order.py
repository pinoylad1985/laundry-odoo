import json

from odoo import models, fields, api
from odoo.exceptions import ValidationError

# The laundry_status values that mean the work is still open. An order with no per-service
# rows is pending only while its status is one of these - asking it this way round (rather
# than "is it finished?") keeps a legacy status nobody has seen before, or a blank one,
# out of Orders Pending. Kept in step with LAUNDRY_OPEN_STATUSES in laundry_pos/__init__.py,
# which the migration and the post-init hook recompute the roll-up with in SQL.
LAUNDRY_OPEN_STATUSES = ('Not Started', 'In Process')


class PosOrder(models.Model):
    _inherit = 'pos.order'

    laundry_customer_type = fields.Selection(
        selection=[
            ('new', 'New Customer'),
            ('returning', 'Returning Customer'),
        ],
        string='Customer Type',
    )
    laundry_service_type = fields.Selection(
        selection=[
            ('dropoff', 'Drop-off'),
            ('dropoff_delivery', 'Drop-off & Delivery'),
            ('pickup_delivery', 'Pickup & Delivery'),
            ('locker', 'Locker'),
            ('self_service', 'Self-service'),
            # Non-service categories — set via the list-view dropdown / legacy
            # x_studio_category, NOT offered in the New Order modal.
            ('payment', 'Payment'),
            ('adjustment', 'Adjustment'),
        ],
        string='Service Type',
    )
    # Coarse classification for reporting: Order (a real service sale) /
    # Payment (created via Settle Order) / Refund (a refund or a refunded order).
    # Refund wins over Order, so a service order that gets refunded flips to Refund.
    laundry_secondary_type = fields.Selection(
        selection=[
            ('order', 'Order'),
            ('payment', 'Payment'),
            ('adjustment', 'Adjustment'),
            ('refund', 'Refund'),
        ],
        string='Secondary Type',
        compute='_compute_laundry_secondary_type',
        store=True,
    )

    # --- New Order details, set from the POS modal submit (see
    # static/src/overrides/product_screen_patch.js) so they persist & are reportable. ---
    laundry_turnaround = fields.Selection(
        selection=[
            ('express', 'Express'),
            ('regular', 'Regular'),
        ],
        string='Turnaround (TAT)',
    )
    laundry_services = fields.Char(string='Laundry Services')  # comma-joined service names
    laundry_claim_datetime = fields.Datetime(string='Claim Date & Time')
    laundry_pickup_datetime = fields.Datetime(string='Pickup Date & Time')
    laundry_delivery_datetime = fields.Datetime(string='Delivery Date & Time')

    # --- Server-derived (no POS involvement) ---
    laundry_due_datetime = fields.Datetime(
        string='Due Date',  # UI label only; internal field stays laundry_due_datetime
        compute='_compute_laundry_due_datetime',
        store=True,
    )
    # Computed-stored (NOT related) on purpose: a stored `related` field is
    # writable and would push values back to res.partner — POS could wipe the
    # customer's phone on order sync. A computed field has no inverse, so it's
    # one-way (partner -> order) and a stale value sent by POS is corrected on
    # the next recompute.
    laundry_customer_phone = fields.Char(
        string='Customer Phone',
        compute='_compute_laundry_customer_phone',
        store=True,
    )
    laundry_customer_address = fields.Char(
        string='Customer Address',
        compute='_compute_laundry_customer_address',
        store=True,
    )
    # Display-only column for the POS Orders list: customer name + phone + address stacked
    # in one wrapped cell (rendered by the `laundry_customer_block` OWL widget), as a
    # SEPARATE optional column alongside the core "Customer" (partner_id) column. Labelled
    # distinctly ("Customer Details") so it's not confused with "Customer" in the ⚙ column
    # menu. NOT stored — so it never triggers a mass recompute / write_date bump across all
    # orders; the widget reads partner_id / laundry_customer_phone / laundry_customer_address
    # directly. The Char value is just an export/fallback.
    laundry_customer_block = fields.Char(
        string='Customer Details',
        compute='_compute_laundry_customer_block',
    )

    # --- Back-office workflow fields (migrating off the Studio x_studio_* equivalents;
    # selection values mirror the Studio fields so legacy data copies across 1:1). ---
    # Legacy free-text staff name (migrated from x_studio_staff); superseded by the
    # employee link below. Kept for history — it holds names with no matching employee
    # (e.g. Kate). Not shown in the views.
    laundry_staff = fields.Selection(
        selection=[
            ('Maan', 'Maan'), ('Jane', 'Jane'), ('Rose', 'Rose'), ('Yumi', 'Yumi'),
            ('Beth', 'Beth'), ('Nita', 'Nita'), ('Emy', 'Emy'),
            ('Ruby', 'Ruby'), ('Tesheil', 'Tesheil'), ('Mark', 'Mark'),
            ('Jepoy', 'Jepoy'), ('Mona', 'Mona'), ('Tricia', 'Tricia'),
        ],
        string='Staff (legacy name)',
    )
    # --- Staff / Processed Date / Status. These three are the LIST COLUMNS the laundry
    # reads progress in, and in the list each cell now shows one line per service (widgets
    # laundry_service_staff / _processed / _status, fed by laundry_service_block below).
    # The VALUES here stay what they always were: this order's Wash-Dry-Fold progress -
    # the staff PIN dialog was titled "Wash-Dry-Fold Staff" from the start - kept as a
    # READ-ONLY, one-way mirror of the order's Wash-Dry-Fold service row (see
    # laundry_order_service._mirror_wdf_to_order). They are no longer written directly:
    # tap a service line, which writes that row, and the mirror follows.
    # Kept stored (not computed) so the legacy values already in the database are never
    # recomputed away, and so laundry_account_reports keeps reading laundry_staff_id. ---
    laundry_staff_id = fields.Many2one('hr.employee', string='Staff')
    laundry_processed_datetime = fields.Datetime(string='Processed Date')
    laundry_status = fields.Selection(
        selection=[
            ('Not Started', 'Not Started'),
            ('In Process', 'In Process'),
            # Stored value stays 'Folded' - relabelled "Done" because an order now holds
            # services that are never folded (Press, Shoe Clean). Label-only, so years of
            # legacy rows need no migration; laundry.order.service uses 'Done' outright.
            ('Folded', 'Done'),
        ],
        string='Status',
        default='Not Started',
    )

    # --- Per-service progress (multiple services per order) ---
    # One row per service on the order, each with its own staff, processed date and status.
    laundry_service_ids = fields.One2many(
        'laundry.order.service', 'order_id', string='Service Tracking',
    )
    # What feeds the Staff / Processed Date / Status cells: every service on the order
    # with its own staff, processed date and status. NO column of its own - it is carried
    # in the list arch as a hidden column and the three real columns above do the showing.
    #
    # A JSON string, NOT the One2many itself: a list view gives a cell widget only the
    # fields declared in the arch, and there is no way to declare the sub-fields of an
    # x2many inside a list cell, so the services have to arrive as a value on the order.
    # Same trick the `laundry_services_list` column already uses; JSON rather than a
    # delimiter because staff and service names are free text that would collide with any
    # separator worth reading. Display-only and NOT stored - it would otherwise bump
    # write_date on every order whenever a service moved.
    laundry_service_block = fields.Char(
        string='Service Progress (internal)',
        compute='_compute_laundry_service_block',
    )
    # THE PENDING TEST. An order is finished only when every one of its services is Done;
    # until then it is pending. Stored so the Orders Pending list and the PENDINGS filter
    # can search it. An order with no service rows at all counts as NOT done, which is how
    # a fresh order behaved when this was a single 'Not Started' status.
    laundry_all_done = fields.Boolean(
        string='All Services Done',
        compute='_compute_laundry_all_done',
        store=True,
    )
    # Due-urgency icon for the pending-orders list (module replacement for the Studio
    # x_studio_due_icon). Non-stored + time-based: recomputed on each read so it always
    # reflects "now" — 🚨PD (past due) / ⏰3hrs / ⏳3-6hrs for open, due-soon orders.
    laundry_due_icon = fields.Char(
        string='Due Icon',
        compute='_compute_laundry_due_icon',
    )
    # Plain urgency code ('pd'/'soon'/'near'/'') paired with the icon above — drives the
    # pastel row colour in the Orders list (decoration-* in the arch + backend SCSS). Non-stored.
    laundry_due_level = fields.Char(
        string='Due Level',
        compute='_compute_laundry_due_icon',
    )
    # Pickup rider — signs off on a Pickup & Delivery / Locker order at payment (POS PIN gate).
    laundry_pickup_rider = fields.Char(string='Pickup Rider')
    # Delivery rider — captured by the delivery sign-off (process to follow); blank until then.
    laundry_delivery_rider = fields.Char(string='Delivery Rider')

    # --- Refund control (set on the REFUND order when a paid order is refunded) ---
    # Either the rebooked replacement order is referenced (normal path) OR a manager
    # approved the refund without a rebooking (override path) — see the refund gate.
    laundry_refund_rebook_ref = fields.Char(string='Refund Rebook Ref')  # rebooked order name (+tracking)
    laundry_refund_manager = fields.Char(string='Refund Approved By')     # manager (override path only)
    laundry_refund_reason = fields.Char(string='Refund Reason')           # reason (override path only)

    # --- Payment control ---
    # Customer Account (pay-later) tender on a Drop-off / Drop-off & Delivery order requires
    # a manager PIN at payment. Set to the approving manager's name (see the payment screen
    # patch + manager_gate popup). Blank = never needed one (any other service type, or paid
    # by cash/card). Set frontend-side and synced like the other stored pos.order fields.
    laundry_account_approved_by = fields.Char(string='Account Approved By')

    @api.depends('laundry_service_ids.status', 'laundry_status')
    def _compute_laundry_all_done(self):
        for order in self:
            services = order.laundry_service_ids
            if services:
                # What pending MEANS: every service on the order has to be Done.
                order.laundry_all_done = all(s.status == 'Done' for s in services)
            else:
                # No service rows: orders from before per-service tracking, and any order
                # the POS never sent a service list for. Fall back to the order's own
                # status - and ask whether it is still OPEN, not whether it is finished.
                # Asking it the other way round ('Folded'?) is what put 161 finished
                # orders from 2025-10 back into Orders Pending: their status was the
                # legacy 'Done' (see _laundry_normalize_status), which is neither 'Folded'
                # nor an open status. Phrased like this, only a status that actually says
                # the work is open can hold an order in the pending list.
                order.laundry_all_done = order.laundry_status not in LAUNDRY_OPEN_STATUSES

    @api.depends('laundry_service_ids.name', 'laundry_service_ids.staff_id',
                 'laundry_service_ids.processed_datetime', 'laundry_service_ids.status')
    def _compute_laundry_service_block(self):
        for order in self:
            order.laundry_service_block = json.dumps([
                {
                    # The row id is what the cell taps through to - the widget opens the
                    # PIN wizard on THIS service rather than on the order.
                    'id': s.id,
                    'name': s.name,
                    'staff': s.staff_id.name or '',
                    'staff_id': s.staff_id.id or False,
                    # Naive UTC, as stored. The widget converts to the user's timezone;
                    # formatting it here would bake the server's timezone into the cell.
                    'processed': (
                        fields.Datetime.to_string(s.processed_datetime)
                        if s.processed_datetime else ''
                    ),
                    'status': s.status,
                }
                for s in order.laundry_service_ids
            ])

    def _sync_laundry_services(self):
        """Derive the per-service tracking rows from `laundry_services`.

        `laundry_services` is the comma-joined, de-duplicated list of service LABELS the
        POS writes at order submit (product_screen_patch._getLaundryServices), in POS-menu
        order - so it is both the list of services to track and the order to show them in.
        """
        Service = self.env['laundry.order.service']
        for order in self:
            wanted = list(dict.fromkeys(
                n.strip() for n in (order.laundry_services or '').split(',') if n.strip()
            ))
            existing = {s.name: s for s in order.laundry_service_ids}
            for index, name in enumerate(wanted):
                sequence = (index + 1) * 10
                service = existing.pop(name, None)
                if service:
                    if service.sequence != sequence:
                        service.sequence = sequence
                else:
                    Service.create({
                        'order_id': order.id, 'name': name, 'sequence': sequence,
                    })
            # Rows for services no longer on the order. Only the UNTOUCHED ones go: work
            # somebody already signed for is never erased just because the order was
            # re-configured, it stays on the record as history.
            stale = Service.browse([
                s.id for s in existing.values()
                if not s.staff_id and not s.processed_datetime
            ])
            stale.unlink()

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._sync_laundry_services()
        return orders

    def write(self, vals):
        res = super().write(vals)
        # Only when the service list itself moves. The WDF mirror writes the three legacy
        # fields back onto the order, so syncing on every write would recurse.
        if 'laundry_services' in vals:
            self._sync_laundry_services()
        return res

    @api.depends('laundry_delivery_datetime', 'laundry_claim_datetime')
    def _compute_laundry_due_datetime(self):
        # Legacy fallback to the ex-Studio field for old records. Guarded by a
        # field-existence check so the module stays installable without Studio;
        # x_studio_due_datetime is intentionally NOT in @api.depends (it may not
        # exist, and legacy values don't change).
        has_legacy = 'x_studio_due_datetime' in self._fields
        for order in self:
            legacy = order.x_studio_due_datetime if has_legacy else False
            # Delivery time wins (delivery-type services); else claim; else legacy.
            order.laundry_due_datetime = (
                order.laundry_delivery_datetime
                or order.laundry_claim_datetime
                or legacy
            )

    @api.depends('laundry_all_done', 'laundry_due_datetime')
    def _compute_laundry_due_icon(self):
        # Same buckets as the legacy Studio x_studio_due_icon, but off the module
        # fields. Non-stored, so it recomputes on every read (list load) — the value
        # tracks the current time even though nothing can @api.depends on "now".
        HOUR = 3600
        now = fields.Datetime.now()  # naive UTC — matches Odoo's datetime storage
        for order in self:
            icon, level = "", ""
            # Urgency is about work still owed, so it follows the roll-up: the icon
            # clears only once EVERY service is Done, not once Wash-Dry-Fold is.
            if not order.laundry_all_done and order.laundry_due_datetime:
                delta = (order.laundry_due_datetime - now).total_seconds()
                if delta < 0:
                    icon, level = "🚨PD", "pd"
                elif delta <= 3 * HOUR:
                    icon, level = "⏰3hrs", "soon"
                elif delta <= 6 * HOUR:
                    icon, level = "⏳3-6hrs", "near"
            order.laundry_due_icon = icon
            order.laundry_due_level = level

    @api.depends('is_refund', 'laundry_service_type', 'lines.refund_orderline_ids',
                 'lines.settled_order_id', 'lines.settled_invoice_id', 'lines.product_id')
    def _compute_laundry_secondary_type(self):
        # Legacy orders carry their category on the Studio field x_studio_category
        # ("Payment" / "Adjustment" / the 5 service-type labels). Read it when present
        # (guarded — it may not exist without Studio) but DON'T depend on it: it's
        # historical data that never changes.
        has_studio_cat = 'x_studio_category' in self._fields
        service_codes = ('dropoff', 'dropoff_delivery', 'pickup_delivery', 'locker', 'self_service')
        service_labels = ('Drop-off', 'Drop-off & Delivery', 'Pickup & Delivery', 'Locker', 'Self-service')
        for order in self:
            deposit = order.config_id.deposit_product_id
            is_settle = any(
                line.settled_order_id or line.settled_invoice_id
                or (deposit and line.product_id == deposit)
                for line in order.lines
            )
            # "Refunded" = some line of this order has refund lines pointing at it. We depend
            # on lines.refund_orderline_ids (not the NON-stored refund_orders_count) so the
            # stored value actually recomputes when a refund is created against this order.
            was_refunded = any(line.refund_orderline_ids for line in order.lines)
            studio_cat = order.x_studio_category if has_studio_cat else False
            if order.is_refund or was_refunded:
                # A refund, or an order that has been refunded → Refund (wins over all).
                order.laundry_secondary_type = 'refund'
            elif is_settle or order.laundry_service_type == 'payment' or studio_cat == 'Payment':
                # Settle Order (new) or a Payment tag (new or legacy Studio).
                order.laundry_secondary_type = 'payment'
            elif order.laundry_service_type == 'adjustment' or studio_cat == 'Adjustment':
                # Adjustment (new or legacy Studio) — kept for prior orders.
                order.laundry_secondary_type = 'adjustment'
            elif order.laundry_service_type in service_codes or studio_cat in service_labels:
                # Has a real service type (new module or legacy Studio label) → a sale.
                order.laundry_secondary_type = 'order'
            else:
                # No category at all → leave blank (uncategorized).
                order.laundry_secondary_type = False

    @api.depends('partner_id.phone')
    def _compute_laundry_customer_phone(self):
        for order in self:
            order.laundry_customer_phone = order.partner_id.phone or False

    @api.depends('partner_id.street', 'partner_id.street2')
    def _compute_laundry_customer_address(self):
        for order in self:
            p = order.partner_id
            order.laundry_customer_address = (
                " ".join(filter(None, [p.street, p.street2])).strip() or False
            )

    @api.depends('partner_id.display_name', 'laundry_customer_phone', 'laundry_customer_address')
    def _compute_laundry_customer_block(self):
        for order in self:
            parts = filter(None, [
                order.partner_id.display_name,
                order.laundry_customer_phone,
                order.laundry_customer_address,
            ])
            order.laundry_customer_block = " · ".join(parts) or False

    def action_laundry_set_staff(self):
        """Open the PIN-gated wizard to set Staff / Folding Time on this order."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Set Staff / Processed Date",
            "res_model": "laundry.staff.pin.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_order_id": self.id,
                "default_staff_id": self.laundry_staff_id.id,
                "default_processed_datetime": self.laundry_processed_datetime,
            },
        }

    @api.model
    def get_laundry_riders(self):
        """Active employees tagged as riders — for the POS sign-off pills."""
        riders = self.env["hr.employee"].sudo().search([("is_laundry_rider", "=", True)])
        return [{"id": r.id, "name": r.name} for r in riders]

    @api.model
    def check_laundry_rider(self, pin, rider_id=False):
        """Authenticate a rider like the register unlock: with rider_id, verify THAT rider's
        PIN; otherwise find the tagged rider whose PIN matches (type-PIN-to-identify).
        Returns {'id', 'name'} on success, else False."""
        pin = (pin or "").strip()
        Emp = self.env["hr.employee"].sudo()
        if rider_id:
            emp = Emp.browse(int(rider_id))
            ok = emp.exists() and emp.is_laundry_rider and emp.pin and emp.pin == pin
            return {"id": emp.id, "name": emp.name} if ok else False
        emp = Emp.search([("is_laundry_rider", "=", True), ("pin", "=", pin)], limit=1)
        return {"id": emp.id, "name": emp.name} if emp else False

    @api.model
    def get_laundry_staff(self):
        """Active employees — for the Staff PIN dialog's name list."""
        emps = self.env["hr.employee"].sudo().search([])
        return [{"id": e.id, "name": e.name} for e in emps]

    @api.model
    def check_laundry_staff(self, pin, staff_id=False):
        """Authenticate a staff member by PIN (or verify the selected one). Returns
        {'id', 'name'} on success, else False."""
        pin = (pin or "").strip()
        Emp = self.env["hr.employee"].sudo()
        if staff_id:
            emp = Emp.browse(int(staff_id))
            ok = emp.exists() and emp.pin and emp.pin == pin
            return {"id": emp.id, "name": emp.name} if ok else False
        emp = Emp.search([("pin", "=", pin)], limit=1)
        return {"id": emp.id, "name": emp.name} if emp else False

    @api.model
    def laundry_service_counts(self, domain, services):
        """How many orders in `domain` carry each of `services` — the numbers on the
        Orders Pending stat buttons.

        `laundry_services` is the comma-joined list of service NAMES the POS writes on
        the order, so each service is counted with the same `ilike` the matching
        search-view filter uses; the two can never drift apart and give a button a
        count its own filter would not produce. An order with three services counts
        once under each of them, which is why these do not add up to `total`.

        One call rather than one per service: the bar needs six numbers and the list
        it sits on top of is already fetching a page of records.
        """
        domain = domain or []
        return {
            "total": self.search_count(domain),
            "services": {
                name: self.search_count(
                    domain + [("laundry_services", "ilike", name)]
                )
                for name in (services or [])
            },
        }

    @api.model
    def check_laundry_manager(self, pin):
        """Authenticate a MANAGER by PIN — for approving a refund without a rebooked
        order. Returns {'id', 'name'} if the PIN belongs to an employee flagged
        is_laundry_manager, else False."""
        pin = (pin or "").strip()
        emp = self.env["hr.employee"].sudo().search(
            [("is_laundry_manager", "=", True), ("pin", "=", pin)], limit=1
        )
        return {"id": emp.id, "name": emp.name} if emp else False

    @api.model
    def check_laundry_rebook(self, original_id, tracking_number, same_customer=True):
        """Validate a rebooked replacement order before a refund is allowed.

        The rebooked order must have the given ``tracking_number``, a LATER
        ``date_order`` than the order being refunded, and a positive total (a real
        sale, not a refund/void). ``tracking_number`` alone is NOT unique in Odoo, so
        the later-date filter — and, on the same-customer path, the customer match —
        are what actually pin it down.

        ``same_customer=True`` (Rebooked order · same customer) also requires the
        rebooking to belong to the SAME customer as the order being refunded.
        ``same_customer=False`` (Rebooked order · different customer) drops that
        filter — the rebooking may be under a different customer (the UI additionally
        requires a manager PIN on that path). Returns:
          {'status': 'ok', 'name', 'tracking_number'}  — exactly one match (approve)
          {'status': 'none'}                            — no match (block)
          {'status': 'ambiguous', 'count'}             — >1 match, can't confirm (block)
          {'status': 'no_customer'}                     — same-customer path, original has no customer (block)
        """
        original = self.browse(int(original_id))
        if not original.exists():
            return {"status": "none"}
        if same_customer and not original.partner_id:
            return {"status": "no_customer"}
        tn = (tracking_number or "").strip()
        if not tn:
            return {"status": "none"}
        domain = [
            ("tracking_number", "=", tn),
            ("date_order", ">", original.date_order),
            ("amount_total", ">", 0),   # a real sale, not a refund/void
            ("id", "!=", original.id),
        ]
        if same_customer:
            domain.append(("partner_id", "=", original.partner_id.id))
        matches = self.search(domain)
        if len(matches) == 1:
            return {
                "status": "ok",
                "name": matches.name,
                "tracking_number": matches.tracking_number,
            }
        if not matches:
            return {"status": "none"}
        return {"status": "ambiguous", "count": len(matches)}

    @api.model
    def get_laundry_refund_payments(self, original_id):
        """Return the payment lines of the order being refunded so the refund can be
        locked to the EXACT same tender(s). Partial refunds are disabled, so the refund
        mirrors the original 1:1 — each of these is applied negated on the refund.
        Returns [{'payment_method_id': id, 'amount': float}, ...]."""
        original = self.browse(int(original_id))
        if not original.exists():
            return []
        return [
            {"payment_method_id": p.payment_method_id.id, "amount": p.amount}
            for p in original.payment_ids
        ]

    @api.constrains("laundry_processed_datetime")
    def _check_laundry_processed_not_future(self):
        now = fields.Datetime.now()
        for order in self:
            if order.laundry_processed_datetime and order.laundry_processed_datetime > now:
                raise ValidationError("Processed Date cannot be in the future.")

    # NOTE: No _load_pos_data_fields override needed here.
    # pos.order's default returns [] which means Odoo reads ALL fields via read([]).
    # Our selection fields are automatically included in that full read.


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    # The real weighed value the cashier enters in the WDF configurator, shown
    # as-is (like a variant attribute) in the cart/receipt. The line QTY is the
    # billed value (this rounded UP to the nearest 0.5 kg).
    laundry_actual_weight = fields.Float(string='Actual Weight (KG)')

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if 'laundry_actual_weight' not in fields_list:
            fields_list.append('laundry_actual_weight')
        return fields_list
