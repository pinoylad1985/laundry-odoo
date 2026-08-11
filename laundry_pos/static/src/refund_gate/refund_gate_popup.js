/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

// Refund control gate. Before a paid order is refunded the cashier must pick one path
// (all three require a typed REASON):
//  (a) 'rebook'       — Rebooked order (same customer): reference the rebooked replacement
//                       order by its number; validated server-side (same tracking # + SAME
//                       customer + a later date_order, since tracking # isn't unique).
//  (b) 'rebook_other' — Rebooked order (different customer): reference the rebooked order
//                       (created AFTER this one, ANY customer) AND a MANAGER PIN.
//  (c) 'override'     — No rebooking: a MANAGER PIN only (no rebooked order).
// Resolves via onApproved({mode, reason, [rebookRef], [manager]}).
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
            mode: "rebook", // 'rebook' | 'rebook_other' | 'override'
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

    // Turn a non-ok check_laundry_rebook result into a user-facing error.
    _rebookError(res, kind) {
        if (res && res.status === "ambiguous") {
            this.state.error =
                `${res.count} orders match that number` +
                (kind === "same" ? " for this customer" : "") +
                ` — can't confirm which is the rebooking. Use "No rebooking" instead.`;
        } else if (res && res.status === "no_customer") {
            this.state.error = "This order has no customer, so it can't be matched. Use manager approval.";
        } else {
            this.state.error =
                kind === "same"
                    ? "No matching rebooked order (same customer, created after this one)."
                    : "No matching rebooked order (created after this one).";
        }
    }

    async confirm() {
        if (this.state.busy) {
            return;
        }
        this.state.error = "";

        const reason = (this.state.reason || "").trim();
        if (!reason) {
            this.state.error = "Enter a reason.";
            return;
        }

        // (a) Rebooked order (same customer) — reason + rebooked order (same customer, later).
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
                    true,
                ]);
            } finally {
                this.state.busy = false;
            }
            if (res && res.status === "ok") {
                this.props.onApproved({
                    mode: "rebook",
                    rebookRef: `${res.name} (${res.tracking_number})`,
                    reason,
                });
                this.props.close();
                return;
            }
            this._rebookError(res, "same");
            return;
        }

        // (b) Rebooked order (different customer) — reason + rebooked order (later, any
        //     customer) + manager PIN.
        if (this.state.mode === "rebook_other") {
            const tn = (this.state.tracking || "").trim();
            if (!tn) {
                this.state.error = "Enter the rebooked order number.";
                return;
            }
            if (!this.state.pin) {
                this.state.error = "Enter the manager PIN.";
                return;
            }
            this.state.busy = true;
            try {
                const res = await this.orm.call("pos.order", "check_laundry_rebook", [
                    this.props.originalOrderId,
                    tn,
                    false,
                ]);
                if (!res || res.status !== "ok") {
                    this._rebookError(res, "other");
                    return;
                }
                const mgr = await this.orm.call("pos.order", "check_laundry_manager", [this.state.pin]);
                if (!mgr) {
                    this.state.error = "Not a manager PIN.";
                    this.state.pin = "";
                    return;
                }
                this.props.onApproved({
                    mode: "rebook_other",
                    rebookRef: `${res.name} (${res.tracking_number})`,
                    manager: mgr.name,
                    reason,
                });
                this.props.close();
            } finally {
                this.state.busy = false;
            }
            return;
        }

        // (c) No rebooking — manager PIN + reason.
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
