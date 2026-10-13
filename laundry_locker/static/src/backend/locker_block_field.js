/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

// Display-only POS Orders list widget for the `laundry_locker_block` ("Locker Details")
// column: the drop-off's ref, then the site it came from, then the door it was in,
// stacked in one wrapped cell in place of three columns of their own.
//
// Reads the three source fields rather than splitting the Char the server computes, so
// the door can be labelled and a site that one day contains the separator cannot break
// the lines apart. They are declared as fieldDependencies so they load even though they
// no longer have columns of their own.
export class LockerBlockField extends Component {
    static template = "laundry_locker.LockerBlockField";
    static props = { ...standardFieldProps };

    get ref() {
        return this.props.record.data.laundry_locker_ref || "";
    }
    get site() {
        return this.props.record.data.laundry_locker_location || "";
    }
    get door() {
        return this.props.record.data.laundry_locker_door || "";
    }
}

registry.category("fields").add("laundry_locker_block", {
    component: LockerBlockField,
    supportedTypes: ["char"],
    fieldDependencies: [
        { name: "laundry_locker_ref", type: "char" },
        { name: "laundry_locker_location", type: "char" },
        { name: "laundry_locker_door", type: "char" },
    ],
});
