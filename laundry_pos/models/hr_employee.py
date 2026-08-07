from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    # Laundry role flags — tag an employee with the laundry role(s) they hold. Wired
    # behaviour today: `is_laundry_rider` (rider sign-off at payment) and
    # `is_laundry_manager` (refund control gate override). The others are role tags for
    # classification / future workflow gating.
    is_laundry_attendant = fields.Boolean(
        string="Laundry Attendant",
        help="Attendant — wash-floor / front-desk staff who receive and process laundry orders.",
    )
    is_laundry_cashier = fields.Boolean(
        string="Laundry Cashier",
        help="Cashier — operates the POS register (takes orders and payments).",
    )
    is_laundry_rider = fields.Boolean(
        string="Laundry Rider",
        help="Delivery rider — appears in the rider sign-off at payment for "
             "Pickup & Delivery / Locker orders (signs off with their POS PIN).",
    )
    is_laundry_manager = fields.Boolean(
        string="Laundry Manager",
        help="Manager — may approve a refund WITHOUT a rebooked order (with their "
             "POS PIN + a reason) in the refund control gate.",
    )
    is_laundry_admin = fields.Boolean(
        string="Laundry Admin",
        help="Admin — full administrative access to the laundry workflow.",
    )
