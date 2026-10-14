from odoo import api, fields, models

# The order's own laundry_staff_id / laundry_processed_datetime / laundry_status describe
# THIS service and no other — they always did (the staff PIN dialog is titled
# "Wash-Dry-Fold Staff"). They are now kept as a one-way mirror of this service's row so
# the columns, the reports and the Studio layout that read them keep working.
WDF_SERVICE = "Wash-Dry-Fold"


class LaundryOrderService(models.Model):
    """One service on an order, tracked on its own.

    An order may carry several services (Wash-Dry-Fold + Press + ...), handled by
    different people and finished at different times, so staff / processed date / status
    cannot be single values on the order any more. They live here, one row per service,
    and the order rolls them up into `laundry_all_done` — which is what "pending" now
    means: an order is pending until EVERY one of its services is Done.

    The service is held as its NAME, not a link to a product or to laundry.service.type.
    That is how the rest of the module recognises services (see
    utils/laundry_instructions.js) and it is the shape the POS already sends in
    `laundry_services`, so the rows derive from a field we have always stored and survive
    a database restore or a re-seed with nothing to keep in sync.
    """
    _name = "laundry.order.service"
    _description = "Laundry service tracked on an order"
    # Services read in POS-menu order (Wash-Dry-Fold, Dry/Wet Clean, Shoe, Cap, Press),
    # which is the order `laundry_services` is written in — see _sync_laundry_services.
    _order = "order_id, sequence, id"

    order_id = fields.Many2one(
        "pos.order", string="Order", required=True, ondelete="cascade", index=True,
    )
    name = fields.Char(string="Service", required=True)
    sequence = fields.Integer(string="Sequence", default=10)
    staff_id = fields.Many2one("hr.employee", string="Staff")
    processed_datetime = fields.Datetime(string="Processed Date")
    # Derived, never typed — the same rule the retired "Orders Status Update" automation
    # applied to the order: staff assigned = In Process, staff + a processed date = Done.
    # Computed-stored rather than automated so the status can never drift from the two
    # values it describes, and so it is searchable for the pending roll-up.
    status = fields.Selection(
        selection=[
            ("Not Started", "Not Started"),
            ("In Process", "In Process"),
            # 'Done', not the order field's legacy 'Folded': this table is new, so there
            # is no history to migrate, and "Folded" only ever described Wash-Dry-Fold.
            ("Done", "Done"),
        ],
        string="Status",
        compute="_compute_status",
        store=True,
        default="Not Started",
    )

    # Odoo 19: _sql_constraints is ignored (it only logs a warning), so the uniqueness
    # has to be declared as a models.Constraint. One row per service per order — the rows
    # are derived from a de-duplicated list, and the migration relies on this index.
    _service_uniq = models.Constraint(
        "unique(order_id, name)",
        "A service can only be tracked once per order.",
    )

    @api.depends("staff_id", "processed_datetime")
    def _compute_status(self):
        for svc in self:
            if svc.staff_id and svc.processed_datetime:
                svc.status = "Done"
            elif svc.staff_id:
                svc.status = "In Process"
            else:
                svc.status = "Not Started"

    @api.model_create_multi
    def create(self, vals_list):
        services = super().create(vals_list)
        services._mirror_wdf_to_order()
        return services

    def write(self, vals):
        res = super().write(vals)
        # Only the three mirrored values matter; a sequence shuffle must not rewrite the
        # order (and so bump its write_date) for nothing.
        if {"name", "staff_id", "processed_datetime"} & set(vals):
            self._mirror_wdf_to_order()
        return res

    def _mirror_wdf_to_order(self):
        """Push the Wash-Dry-Fold row onto the order's legacy WDF fields.

        Deliberately ONE-WAY (row -> order). A two-way sync would need the order fields to
        be writable, and then two places could disagree about the same fact; this way the
        row is the only source of truth and the order fields are a read-only copy for the
        list columns, the Studio layout and laundry_account_reports (which reads
        laundry_staff_id as the cashier).
        """
        for svc in self:
            if svc.name != WDF_SERVICE or not svc.order_id:
                continue
            svc.order_id.write({
                "laundry_staff_id": svc.staff_id.id,
                "laundry_processed_datetime": svc.processed_datetime,
                # The order field keeps its stored 'Folded' value (relabelled "Done" in
                # the UI) so a decade of legacy rows needs no migration.
                "laundry_status": "Folded" if svc.status == "Done" else svc.status,
            })
