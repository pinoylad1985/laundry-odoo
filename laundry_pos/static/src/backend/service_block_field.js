/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { deserializeDateTime } from "@web/core/l10n/dates";

// The single "Services Progress" column on the POS Orders lists: every service on the
// order with its own staff, processed date and status, stacked in one cell - and each
// line is a tap target that opens the PIN wizard on THAT service.
//
// The field is a JSON string built server-side (pos_order._compute_laundry_service_block)
// because a list cell widget only receives the fields declared in the arch and an
// x2many's sub-fields cannot be declared inside a cell. See that compute for why JSON
// rather than a delimiter.
export class ServiceBlockField extends Component {
    static template = "laundry_pos.ServiceBlockField";
    static props = { ...standardFieldProps };

    setup() {
        this.action = useService("action");
    }

    // A malformed value renders as an empty cell rather than taking the list down with
    // it - the same defensiveness the configurator's crash guard is built on.
    get services() {
        try {
            return JSON.parse(this.props.record.data[this.props.name] || "[]");
        } catch {
            return [];
        }
    }

    // Stored naive UTC -> the user's timezone. An em dash, not a blank, so an unworked
    // service still occupies its slot and the three values stay readable as a row.
    processedLabel(service) {
        if (!service.processed) {
            return "—";
        }
        return deserializeDateTime(service.processed).toFormat("MMM d, h:mm a");
    }

    statusClass(service) {
        if (service.status === "Done") {
            return "laundry-sb-done";
        }
        return service.status === "In Process" ? "laundry-sb-progress" : "laundry-sb-idle";
    }

    // The wizard is pre-filled with whatever the service already has, so correcting a
    // date does not mean re-picking the staff. It still takes that staff's PIN.
    async open(service) {
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                name: service.name,
                res_model: "laundry.staff.pin.wizard",
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
                context: {
                    default_service_id: service.id,
                    default_staff_id: service.staff_id || false,
                    default_processed_datetime: service.processed || false,
                },
            },
            // Reload so the cell, the WDF mirror columns and the pending roll-up all
            // reflect the change without the user refreshing the page.
            { onClose: () => this.props.record.model.load() }
        );
    }
}

registry.category("fields").add("laundry_service_block", {
    component: ServiceBlockField,
    supportedTypes: ["char"],
});
