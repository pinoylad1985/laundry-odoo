from odoo import models


class AccountAgedReceivableReportHandler(models.AbstractModel):
    _inherit = 'account.aged.receivable.report.handler'

    def _custom_line_postprocessor(self, report, options, lines):
        """Fill the "Address" column on the partner rows of the Aged Receivable report.

        _custom_line_postprocessor runs on both the initial render and the unfold
        path, and before _format_column_values, so setting `no_format` is enough:
        the formatter turns it into the displayed `name`, and the xlsx export
        reads `no_format` directly.
        """
        lines = super()._custom_line_postprocessor(report, options, lines)

        column_index = next(
            (
                index
                for index, column in enumerate(options['columns'])
                if column['expression_label'] == 'partner_address'
            ),
            None,
        )
        if column_index is None:
            # Column removed from the report, or we are running for Aged Payable.
            return lines

        partner_ids = set()
        for line in lines:
            model, model_id = report._get_model_info_from_id(line['id'])
            if model == 'res.partner' and model_id:
                partner_ids.add(model_id)

        if not partner_ids:
            return lines

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

            columns[column_index].update({
                'no_format': address,
                'is_zero': False,
            })

        return lines

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
