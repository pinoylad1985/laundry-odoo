/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

// Turnaround as a pill in the POS Orders list: REG (blue) / EXP (red).
//
// Short labels because the column is read down a long list, not across one row - the
// question it answers is "which of these is rushed?", and a red EXP answers it without
// being read. An order with no turnaround set shows an empty cell rather than a third
// colour: nothing was chosen, so there is nothing to flag.
const TAT_PILLS = {
    regular: { label: "REG", cls: "laundry-tat-reg" },
    express: { label: "EXP", cls: "laundry-tat-exp" },
};

export class TatPillField extends Component {
    static template = "laundry_pos.TatPillField";
    static props = { ...standardFieldProps };

    get pill() {
        return TAT_PILLS[this.props.record.data[this.props.name]] || null;
    }
}

registry.category("fields").add("laundry_tat_pill", {
    component: TatPillField,
    supportedTypes: ["selection"],
});
