from odoo import fields, models
from odoo.exceptions import UserError


class LaundryStaffPinWizard(models.TransientModel):
    """PIN-gated setter for an order's Staff + Folding Time. The assigned cashier's POS
    PIN authorizes the change (setting Staff = the new cashier's PIN; updating Folding Time
    = that order's assigned cashier's PIN — here they're the same person: staff_id)."""
    _name = "laundry.staff.pin.wizard"
    _description = "Set laundry Staff / Processed Date (cashier PIN required)"

    order_id = fields.Many2one("pos.order", required=True, ondelete="cascade")
    staff_id = fields.Many2one("hr.employee", string="Staff", required=True)
    folding_time = fields.Datetime(string="Processed Date")
    pin = fields.Char(string="Cashier PIN", required=True)

    def action_confirm(self):
        self.ensure_one()
        emp = self.staff_id.sudo()
        if not emp.pin or emp.pin != (self.pin or "").strip():
            raise UserError("Incorrect PIN for %s." % (self.staff_id.name or "staff"))
        self.order_id.write({
            "laundry_staff_id": self.staff_id.id,
            "laundry_folding_time": self.folding_time,
            # Status follows staff + folding (staff set -> In Process; + folding -> Folded).
            "laundry_status": "Folded" if self.folding_time else "In Process",
        })
        return {"type": "ir.actions.act_window_close"}
