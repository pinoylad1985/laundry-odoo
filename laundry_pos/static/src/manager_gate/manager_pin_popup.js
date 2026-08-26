/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

// Manager-approval PIN gate: a small single-PIN popup verified server-side against
// employees flagged is_laundry_manager (pos.order.check_laundry_manager — the same check
// the refund gate uses). Resolves via onApproved(managerName). Currently used to authorise
// a Customer Account (pay-later) tender on a Drop-off / Drop-off & Delivery order, but the
// title/body are props so it can gate other manager-only actions later.
export class ManagerPinPopup extends Component {
    static template = "laundry_pos.ManagerPinPopup";
    static components = { Dialog };
    static props = {
        close: Function,
        onApproved: Function,
        title: { type: String, optional: true },
        body: { type: String, optional: true },      // intro line above the (optional) list
        items: { type: Array, optional: true },       // rendered as a bulleted list under `body`
        note: { type: String, optional: true },       // closing line below the list
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ pin: "", error: "", busy: false });
    }

    get title() {
        return this.props.title || "Manager Approval";
    }
    get body() {
        return this.props.body || "This action requires a manager PIN.";
    }
    get items() {
        return this.props.items || [];
    }
    get note() {
        return this.props.note || "";
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.confirm();
        }
    }

    async confirm() {
        if (this.state.busy) {
            return;
        }
        if (!this.state.pin) {
            this.state.error = "Enter the manager PIN.";
            return;
        }
        this.state.busy = true;
        let mgr;
        try {
            mgr = await this.orm.call("pos.order", "check_laundry_manager", [this.state.pin]);
        } finally {
            this.state.busy = false;
        }
        if (!mgr) {
            this.state.error = "Not a manager PIN.";
            this.state.pin = "";
            return;
        }
        this.props.onApproved(mgr.name);
        this.props.close();
    }
}
