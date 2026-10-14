from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class LaundryStaffPinWizard(models.TransientModel):
    """PIN-gated setter for Staff + Processed Date on ONE service of an order.

    Opened by tapping a service's line in the Staff / Processed Date / Status columns of
    the POS Orders list. The staff who did that
    service enters their own POS PIN, which both authorizes the change and records who
    did it; the service's status follows from what is filled in (staff = In Process,
    staff + processed date = Done).

    `order_id` is the older, order-level path (action_laundry_set_staff), kept so the
    legacy Wash-Dry-Fold entry point still works on orders that have no service rows.
    """
    _name = "laundry.staff.pin.wizard"
    _description = "Set laundry Staff / Processed Date (cashier PIN required)"

    # NOT readonly: the web client drops readonly fields from the save payload, so a
    # readonly service_id set from the context default would arrive back empty and the
    # wizard would fall through to the order-level branch. It is hidden in the view and
    # shown through service_name instead, the same way order_id has always been carried.
    # "Service Record", not "Service": the label is never shown (the field is invisible
    # and service_name carries the display), and two fields with the same label on one
    # model make Odoo warn on every upgrade.
    service_id = fields.Many2one(
        "laundry.order.service", string="Service Record", ondelete="cascade",
    )
    # Display-only echo of what is being signed for. Related, so it is never written.
    service_name = fields.Char(related="service_id.name", string="Service")
    order_id = fields.Many2one("pos.order", ondelete="cascade")
    staff_id = fields.Many2one("hr.employee", string="Staff", required=True)
    processed_datetime = fields.Datetime(string="Processed Date")
    pin = fields.Char(string="Cashier PIN", required=True)

    @api.constrains("service_id", "order_id")
    def _check_target(self):
        for wizard in self:
            if not wizard.service_id and not wizard.order_id:
                raise ValidationError("Nothing to update: no service and no order.")

    def action_confirm(self):
        self.ensure_one()
        employee = self.staff_id.sudo()
        if not employee.pin or employee.pin != (self.pin or "").strip():
            raise UserError("Incorrect PIN for %s." % (self.staff_id.name or "staff"))
        if self.service_id:
            # Status is computed from these two on the row itself, so it is not set here.
            self.service_id.write({
                "staff_id": self.staff_id.id,
                "processed_datetime": self.processed_datetime,
            })
        else:
            self.order_id.write({
                "laundry_staff_id": self.staff_id.id,
                "laundry_processed_datetime": self.processed_datetime,
                # Legacy path only: the order's stored value is still 'Folded'.
                "laundry_status": "Folded" if self.processed_datetime else "In Process",
            })
        return {"type": "ir.actions.act_window_close"}
