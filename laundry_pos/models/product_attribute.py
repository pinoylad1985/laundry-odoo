from odoo import api, fields, models


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    laundry_express_price = fields.Float(
        string="Express Price",
        digits='Product Price',
        help="Price charged for this option when the order's turnaround is EXPRESS — "
             "used INSTEAD of Extra Price, not on top of it.\n"
             "Leave it at 0 to charge the normal Extra Price on express orders too.",
    )
    # The BGC price book. The two areas price item by item, not by a percentage,
    # so each option carries its own BGC pair beside its Makati pair:
    #
    #            | Regular            | Express
    #   Makati   | Extra Price        | Express Price
    #   BGC      | BGC Price          | BGC Express Price
    #
    # Each BGC box REPLACES its Makati counterpart on a BGC order, and 0/unset
    # falls back to that counterpart - so an option nobody has given a BGC price
    # is charged at the Makati price, and every non-laundry product is untouched.
    #
    # ⚠ These are DIFFERENCES from the product's own Sales Price, exactly like
    # Extra Price. The area difference on Wash-Dry-Fold, Press and Shoe Clean is
    # a flat move of the base price, so it is carried here by raising every
    # option of the one attribute that each line must choose (e.g. WDF soil
    # level: Makati 0/5/10 -> BGC 15/20/25 against a 45 base = 60/65/70). That
    # is why no pricelist is needed anywhere: every laundry product has such an
    # attribute, and the configurator refuses to Add until it is answered.
    laundry_bgc_price = fields.Float(
        string="BGC Price",
        digits='Product Price',
        help="Price charged for this option on a BGC order — used INSTEAD of "
             "Extra Price, not on top of it.\n"
             "Leave it at 0 to charge the normal Extra Price on BGC orders too.",
    )
    laundry_bgc_express_price = fields.Float(
        string="BGC Express Price",
        digits='Product Price',
        help="Price charged for this option on a BGC order whose turnaround is "
             "EXPRESS — used INSTEAD of Express Price (or of Extra Price, where "
             "no Express Price is set).\n"
             "Leave it at 0 to charge the Makati express price on BGC orders too.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        # Loaded into the POS so the configurator can price an express order per
        # item, and a BGC order per item on top of that. ⚠ EXTENDED with super(),
        # never replaced - dropping core's list breaks the configurator entirely.
        return super()._load_pos_data_fields(config) + [
            'laundry_express_price',
            'laundry_bgc_price',
            'laundry_bgc_express_price',
        ]
