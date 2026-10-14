/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { deserializeDateTime, serializeDateTime } from "@web/core/l10n/dates";

// An unworked service still occupies its line: the three columns are read ACROSS as well
// as down, so a blank would slide the lines out of step with the other two columns.
const DASH = "—";

// The POS Orders list keeps its three columns - Staff, Processed Date, Status - and NO
// new column is added for per-service progress. Each cell instead stacks one line per
// service, in the same order in all three columns and in the same order as the Services
// column, so a row still reads left to right: Wash-Dry-Fold / Maria / Oct 14, 3:20 PM /
// Done. One order is still ONE row, however many services it carries.
//
// Every line is a tap target that opens the PIN wizard on THAT service, so progress is
// recorded where it is read.
//
// All three widgets read the SAME field, `laundry_service_block` - a JSON string built by
// pos_order._compute_laundry_service_block and carried in the arch as a hidden column.
// A list cell widget only receives the fields declared in the arch and an x2many's
// sub-fields cannot be declared inside a cell, so the services have to arrive as a value
// on the order itself. The field each widget is attached to is used only for the legacy
// fallback below.
const BLOCK_FIELD = "laundry_service_block";

export class ServiceLinesField extends Component {
    static template = "laundry_pos.ServiceLinesField";
    static props = { ...standardFieldProps };
    // Which of the three values this column shows: "staff" | "processed" | "status".
    static part = "staff";

    setup() {
        this.action = useService("action");
    }

    get part() {
        return this.constructor.part;
    }

    // A malformed value renders as an empty cell rather than taking the whole list down
    // with it - the same defensiveness the POS configurator crash guard is built on.
    get services() {
        try {
            return JSON.parse(this.props.record.data[BLOCK_FIELD] || "[]") || [];
        } catch {
            return [];
        }
    }

    get lines() {
        const services = this.services;
        if (!services.length) {
            return [this.legacyLine()];
        }
        return services.map((service) => ({
            key: service.id,
            text: this.serviceText(service),
            cls: this.part === "status" ? this.statusClass(service.status) : "",
            title: service.name + " — tap to set staff / processed date",
            service,
        }));
    }

    serviceText(service) {
        if (this.part === "staff") {
            return service.staff || DASH;
        }
        if (this.part === "processed") {
            // Stored naive UTC -> the reader timezone. The year is left off: these are
            // read against today, and three of them have to fit inside a list cell.
            return service.processed
                ? deserializeDateTime(service.processed).toFormat("MMM d, h:mm a")
                : DASH;
        }
        return service.status || DASH;
    }

    // Orders that predate per-service tracking have no service rows, so the cell falls
    // back to the order OWN value - the field this widget is actually attached to.
    // Without it, years of finished orders would read as three empty columns.
    legacyLine() {
        const value = this.props.record.data[this.props.name];
        let text = DASH;
        if (this.part === "staff") {
            text = (value && value.display_name) || DASH;
        } else if (this.part === "processed") {
            text = value ? value.toFormat("MMM d, h:mm a") : DASH;
        } else if (value) {
            text = this.selectionLabel(value);
        }
        return {
            key: "legacy",
            text,
            cls: this.part === "status" ? this.statusClass(text) : "",
            title: "Tap to set staff / processed date",
            service: null,
        };
    }

    // The order stored status is still 'Folded', labelled "Done". Read the label off the
    // field itself rather than hard-coding the pair - and fall back to the stored value,
    // so a legacy value that is no longer one of the options is shown as it is instead of
    // blowing up the way the readonly selection renderer in core does.
    selectionLabel(value) {
        const field = this.props.record.fields[this.props.name] || {};
        const option = (field.selection || []).find((o) => o[0] === value);
        return option ? option[1] : value;
    }

    statusClass(status) {
        if (status === "Done") {
            return "laundry-sl-done";
        }
        return status === "In Process" ? "laundry-sl-progress" : "laundry-sl-idle";
    }

    // The wizard opens pre-filled with whatever the service already has, so correcting a
    // date does not mean re-picking the staff. It still takes that staff own PIN.
    async open(line) {
        const context = line.service
            ? {
                  default_service_id: line.service.id,
                  default_staff_id: line.service.staff_id || false,
                  default_processed_datetime: line.service.processed || false,
              }
            : this.legacyContext();
        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                name: line.service ? line.service.name : "Staff / Processed Date",
                res_model: "laundry.staff.pin.wizard",
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
                context,
            },
            // Reload so all three cells, the WDF mirror and the pending roll-up reflect
            // the change without the reader refreshing the page.
            { onClose: () => this.reload() }
        );
    }

    // Reloading is a convenience, never a reason to throw a dialog at somebody who has
    // just signed off a service: whatever happens here, the PIN was already accepted and
    // the change is saved. Worst case the reader refreshes the page themselves.
    async reload() {
        try {
            await this.props.record.model.load();
        } catch {
            try {
                await this.props.record.load();
            } catch {
                // nothing left to try; the record on screen is simply one read stale.
            }
        }
    }

    // No service rows: the wizard writes the order own fields, the way it did before
    // per-service tracking existed.
    legacyContext() {
        const staff = this.props.record.data.laundry_staff_id;
        const processed = this.props.record.data.laundry_processed_datetime;
        return {
            default_order_id: this.props.record.resId,
            default_staff_id: (staff && staff.id) || false,
            default_processed_datetime: processed ? serializeDateTime(processed) : false,
        };
    }
}

export class ServiceStaffLinesField extends ServiceLinesField {
    static part = "staff";
}
export class ServiceProcessedLinesField extends ServiceLinesField {
    static part = "processed";
}
export class ServiceStatusLinesField extends ServiceLinesField {
    static part = "status";
}

registry.category("fields").add("laundry_service_staff", {
    component: ServiceStaffLinesField,
    supportedTypes: ["many2one"],
});
registry.category("fields").add("laundry_service_processed", {
    component: ServiceProcessedLinesField,
    supportedTypes: ["datetime"],
});
registry.category("fields").add("laundry_service_status", {
    component: ServiceStatusLinesField,
    supportedTypes: ["selection"],
});
