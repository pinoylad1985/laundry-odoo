/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { LaundryPinDialog } from "@laundry_pos/backend/laundry_pin_dialog";
import { LaundryFoldingPicker } from "@laundry_pos/backend/laundry_folding_picker";

// Datetime widget for laundry_folding_time: click -> roller date/time picker -> Apply ->
// employee PIN ("Folding Time") -> sets the field. No future (enforced in the picker + a
// server constraint).
export class FoldingPinField extends Component {
    static template = "laundry_pos.FoldingPinField";
    static props = { ...standardFieldProps };

    setup() {
        this.dialog = useService("dialog");
        this.notification = useService("notification");
    }

    get value() {
        return this.props.record.data[this.props.name] || false;
    }
    get display() {
        return this.value ? this.value.toFormat("MMM d, yyyy h:mm a") : "";
    }

    open() {
        this.dialog.add(LaundryFoldingPicker, {
            current: this.value || false,
            onApply: (dt) => this._askPin(dt),
        });
    }
    _askPin(dt) {
        const staff = this.props.record.data.laundry_staff_id;
        if (!staff) {
            this.notification.add("Set the Staff before the Processed Date.", { type: "warning" });
            return;
        }
        this.dialog.add(LaundryPinDialog, {
            title: "Processed Date",
            listMethod: "get_laundry_staff",
            checkMethod: "check_laundry_staff",
            requireId: staff.id,
            requireName: staff.display_name,
            onConfirm: () => this.props.record.update({ [this.props.name]: dt }),
        });
    }
}

registry.category("fields").add("laundry_folding_pin", {
    component: FoldingPinField,
    supportedTypes: ["datetime"],
});
