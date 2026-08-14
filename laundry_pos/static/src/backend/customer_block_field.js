/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

// Display-only POS Orders list widget: stacks the customer name (bold), then phone, then
// address in one wrapped cell. Reads the real partner_id + laundry_customer_* fields
// (declared as fieldDependencies so they load even when their own columns are hidden).
export class CustomerBlockField extends Component {
    static template = "laundry_pos.CustomerBlockField";
    static props = { ...standardFieldProps };

    get name() {
        const p = this.props.record.data.partner_id;
        return p ? p.display_name : "";
    }
    get phone() {
        return this.props.record.data.laundry_customer_phone || "";
    }
    get address() {
        return this.props.record.data.laundry_customer_address || "";
    }
}

registry.category("fields").add("laundry_customer_block", {
    component: CustomerBlockField,
    supportedTypes: ["char"],
    fieldDependencies: [
        { name: "partner_id", type: "many2one" },
        { name: "laundry_customer_phone", type: "char" },
        { name: "laundry_customer_address", type: "char" },
    ],
});
