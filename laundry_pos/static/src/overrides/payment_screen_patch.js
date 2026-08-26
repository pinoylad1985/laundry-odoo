/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ManagerPinPopup } from "@laundry_pos/manager_gate/manager_pin_popup";

// Refund payment lock: a refund must be tendered EXACTLY like the original order — same
// payment method(s) and amount(s), negated (partial refunds are disabled, so the refund
// mirrors the original 1:1). The refund order carries `_laundryLockedPayments`, set by
// ticket_screen_patch.onDoRefund from pos.order.get_laundry_refund_payments. We pre-fill
// those payment lines and block the cashier from adding/removing/re-amounting them.
//
// EVERYTHING here is scoped to locked refund orders — normal (non-refund) payments run
// through `super` untouched.
//
// ⚠ Odoo 19 payment API (verified against core payment_screen.js / pos_payment.js @19.0):
//   order.addPaymentline(method) -> {status, data}   (order-level; also selects the line)
//   order.removePaymentline(line)
//   order.getSelectedPaymentline()
//   line.setAmount(value)                             (camelCase — NOT set_amount)
//   this.currentOrder  (getter, by props.orderUuid)   this.payment_methods_from_config
// The mirror uses the ORDER-level methods directly so the screen-level lock overrides
// below never fight it — no bypass flag needed.

// Service types where a Customer Account (pay-later) tender requires a manager PIN.
// One source of truth for both the gate check and the popup message (kept in sync).
const ACCOUNT_GATED_SERVICE_TYPES = ["dropoff", "dropoff_delivery", "self_service"];
const ACCOUNT_GATED_LABELS = {
    dropoff: "Drop-off",
    dropoff_delivery: "Drop-off & Delivery",
    self_service: "Self-service",
};
const ACCOUNT_GATED_LABEL_LIST = ACCOUNT_GATED_SERVICE_TYPES.map((t) => ACCOUNT_GATED_LABELS[t]);

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = useService("dialog");
        onMounted(() => this._laundryMirrorRefundPayments());
    },

    get _laundryLockedPayments() {
        const locked = this.currentOrder?._laundryLockedPayments;
        return Array.isArray(locked) && locked.length ? locked : null;
    },

    // Pre-fill the refund's payment lines to mirror the original tender(s), negated.
    _laundryMirrorRefundPayments() {
        const order = this.currentOrder;
        const locked = this._laundryLockedPayments;
        if (!locked || !order || order._laundryPaymentsMirrored) {
            return;
        }
        order._laundryPaymentsMirrored = true;
        try {
            const methods = this.payment_methods_from_config || [];
            // Start clean so we end with EXACTLY the mirrored tender(s).
            for (const line of [...order.payment_ids]) {
                order.removePaymentline(line);
            }
            for (const p of locked) {
                const method = methods.find((m) => m.id === p.payment_method_id);
                if (!method) {
                    continue;
                }
                const result = order.addPaymentline(method);
                if (!result || !result.status) {
                    continue;
                }
                const line = order.getSelectedPaymentline();
                if (line) {
                    line.setAmount(-Math.abs(p.amount)); // refund = negated original
                }
            }
        } catch (e) {
            // Never break the payment screen over the mirror — fall back to a manual refund.
            console.warn("laundry_pos: refund payment mirror failed", e);
        }
    },

    // ---- Lock: block manual add / delete / amount edits on a locked refund. ----
    // The mirror above uses order-level methods, so it doesn't pass through these.
    addNewPaymentLine(paymentMethod) {
        if (this._laundryLockedPayments) {
            return false; // locked — the mirrored tender is already set
        }
        // Customer Account (pay-later) tender on a Drop-off / Drop-off & Delivery order needs
        // a manager PIN. Block the add and open the gate; once a manager approves THIS order
        // the tender is allowed freely (add/remove/re-amount) without re-prompting.
        if (this._laundryNeedsAccountApproval(paymentMethod)) {
            this._laundryGateAccountPayment(paymentMethod);
            return false;
        }
        return super.addNewPaymentLine(...arguments);
    },

    // True when the tapped method is Customer Account (pay-later), the order is a Drop-off /
    // Drop-off & Delivery, and no manager has approved on-account for it yet.
    _laundryNeedsAccountApproval(paymentMethod) {
        const order = this.currentOrder;
        return (
            paymentMethod?.type === "pay_later" &&
            ACCOUNT_GATED_SERVICE_TYPES.includes(order?.laundry_service_type) &&
            !order?.laundry_account_approved_by
        );
    },

    // Manager-PIN gate. On approval, record the manager on the order (audit + the "approved"
    // flag) and re-enter addNewPaymentLine so the on-account line is actually added.
    _laundryGateAccountPayment(paymentMethod) {
        const order = this.currentOrder;
        this.dialog.add(ManagerPinPopup, {
            title: "Manager Approval — Customer Account",
            body: "No Customer Account for the following service types:",
            items: ACCOUNT_GATED_LABEL_LIST,
            note: "A manager PIN is required to approve.",
            onApproved: (managerName) => {
                order.laundry_account_approved_by = managerName;
                this.addNewPaymentLine(paymentMethod);
            },
        });
    },

    deletePaymentLine(uuid) {
        if (this._laundryLockedPayments) {
            return; // locked — can't remove the mirrored tender
        }
        return super.deletePaymentLine(...arguments);
    },

    updateSelectedPaymentline(amount) {
        if (this._laundryLockedPayments) {
            return; // locked — amount is fixed to the original
        }
        return super.updateSelectedPaymentline(...arguments);
    },
});
