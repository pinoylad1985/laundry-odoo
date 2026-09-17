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

    @api.model
    def _load_pos_data_fields(self, config):
        # Loaded into the POS so the configurator can price an express order per item.
        return super()._load_pos_data_fields(config) + ['laundry_express_price']
