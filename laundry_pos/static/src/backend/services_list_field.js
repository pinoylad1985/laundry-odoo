/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

// Display-only POS Orders list widget for the `laundry_services` ("Laundry Services")
// column: one service per line instead of one comma-joined run of text.
//
// The field is written by the POS as `codes.join(", ")` (product_screen_patch.js), so a
// comma is the separator and never part of a name - the four are Wash-Dry-Fold,
// Dry/Wet Clean, Shoe Clean and Press, which carry hyphens and a slash but no comma.
// Splitting here rather than storing newlines means every order already in the database
// renders as a list too, with no migration and no second format to keep working.
export class ServicesListField extends Component {
    static template = "laundry_pos.ServicesListField";
    static props = { ...standardFieldProps };

    // Empty pieces are dropped, so a trailing comma or a blank value renders as an
    // empty cell rather than as a stray bullet with nothing after it.
    get services() {
        return (this.props.record.data[this.props.name] || "")
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
    }
}

registry.category("fields").add("laundry_services_list", {
    component: ServicesListField,
    supportedTypes: ["char"],
});
