/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

// A read-only Selection that shows the STORED value when it is not one of the current
// options, instead of crashing.
//
// Core's readonly selection renderer looks the value up in the field's options and reads
// the label straight off the result (`options.find(...)[1]`), so a value that is no
// longer offered - a status left behind by the Studio workflow this module replaced, or
// one removed from a selection since - takes the whole view down with
// "Cannot read properties of undefined (reading '1')". These columns are read over years
// of order history, where exactly that is to be expected.
export class SelectionTextField extends Component {
    static template = "laundry_pos.SelectionTextField";
    static props = { ...standardFieldProps };

    get label() {
        const value = this.props.record.data[this.props.name];
        if (!value) {
            return "";
        }
        const field = this.props.record.fields[this.props.name] || {};
        const option = (field.selection || []).find((o) => o[0] === value);
        return option ? option[1] : value;
    }
}

registry.category("fields").add("laundry_selection_text", {
    component: SelectionTextField,
    supportedTypes: ["selection"],
});
