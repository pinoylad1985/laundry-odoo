/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

// Refund control gate. Before a paid order is refunded the cashier must either:
//  (a) reference the REBOOKED replacement order by its order number — validated server-side
//      (same tracking # + same customer + a later date_order, since tracking # isn't unique); or
//  (b) get a MANAGER to approve a refund with no rebooking (manager PIN + a typed reason).
// Resolves via onApproved({mode:'rebook', rebookRef}) or ({mode:'override', manager, reason}).
export class RefundGatePopup extends Component {
    static template = "laundry_pos.RefundGatePopup";
    static components = { Dialog };
    static props = {
        close: Function,
        onApproved: Function,
        originalOrderId: [Number, String],
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            mode: "rebook", // 'rebook' | 'override'
            tracking: "",
            pin: "",
            reason: "",
            error: "",
            busy: false,
        });
    }

    setMode(mode) {
        this.state.mode = mode;
        this.state.error = "";
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
        this.state.error = "";

        // (a) Rebooked-order path — validate against the original.
        if (this.state.mode === "rebook") {
            const tn = (this.state.tracking || "").trim();
            if (!tn) {
                this.state.error = "Enter the rebooked order number.";
                return;
            }
            this.state.busy = true;
            let res;
            try {
                res = await this.orm.call("pos.order", "check_laundry_rebook", [
                    this.props.originalOrderId,
                    tn,
                ]);
            } finally {
                this.state.busy = false;
            }
            if (res && res.status === "ok") {
                this.props.onApproved({
                    mode: "rebook",
                    rebookRef: `${res.name} (${res.tracking_number})`,
                });
                this.props.close();
                return;
            }
            if (res && res.status === "ambiguous") {
                this.state.error =
                    `${res.count} orders match that number for this customer — can't confirm which is ` +
                    `the rebooking. Use "No rebooking (manager)" instead.`;
            } else if (res && res.status === "no_customer") {
                this.state.error = "This order has no customer, so it can't be matched. Use manager approval.";
            } else {
                this.state.error = "No matching rebooked order (same customer, created after this one).";
            }
            return;
        }

        // (b) Manager-override path — PIN + reason.
        const reason = (this.state.reason || "").trim();
        if (!reason) {
            this.state.error = "Enter a reason.";
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
        this.props.onApproved({ mode: "override", manager: mgr.name, reason });
        this.props.close();
    }
}
