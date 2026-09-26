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

// Service types that are ALWAYS on account, with no choice of tender. A Locker
// customer put the bag in a door and walked away - there is nobody at the till
// to hand over cash, and the sale is settled later (Settle Order). So the
// on-account line is put on for the cashier and held there: any other method
// would be recording money the shop never took.
//
// The mirror image of ACCOUNT_GATED_SERVICE_TYPES above, and deliberately
// disjoint from it - a type either cannot use Customer Account without a
// manager, or can use nothing else.
const ACCOUNT_ONLY_SERVICE_TYPES = ["locker"];
const ACCOUNT_ONLY_MESSAGE =
    "A Locker order is always paid on Customer Account - the tender cannot be changed.";
// The numpad calls updateSelectedPaymentline on every keypress, so the refusal
// is throttled: one explanation, not one per digit.
const ACCOUNT_ONLY_NOTIFY_MS = 4000;

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = useService("dialog");
        this.laundryNotification = useService("notification");
        onMounted(() => {
            this._laundryMirrorRefundPayments();
            this._laundryTenderAccountOnly();
        });
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

    // ---- Account-only service types (Locker): the tender is put on and held. ----
    // The pay-later method this config offers, if it offers one.
    get _laundryAccountMethod() {
        return (this.payment_methods_from_config || []).find(
            (m) => m.type === "pay_later"
        ) || null;
    },

    // The on-account line an account-only order is held to, or null when this
    // is not such an order - INCLUDING when the tender could not be put on at
    // all (no pay-later method in this config, or core refused it for want of
    // a customer). Nothing is locked in that case: a cashier who cannot be
    // given the one right method has to be left able to take the money some
    // other way, rather than handed a screen that refuses everything.
    get _laundryAccountOnlyTender() {
        const order = this.currentOrder;
        if (!order || !ACCOUNT_ONLY_SERVICE_TYPES.includes(order.laundry_service_type)) {
            return null;
        }
        const method = this._laundryAccountMethod;
        const lines = order.payment_ids || [];
        if (!method || lines.length !== 1) {
            return null;
        }
        return lines[0].payment_method_id?.id === method.id ? lines[0] : null;
    },

    _laundryTenderAccountOnly() {
        const order = this.currentOrder;
        // A refund mirrors the ORIGINAL tender, whatever it was, and owns this
        // screen - see _laundryMirrorRefundPayments.
        if (!order || this._laundryLockedPayments) {
            return;
        }
        if (!ACCOUNT_ONLY_SERVICE_TYPES.includes(order.laundry_service_type)) {
            return;
        }
        const method = this._laundryAccountMethod;
        if (!method) {
            return;
        }
        try {
            // Rebuilt, not topped up, on every mount of the screen: the total
            // can have moved while the cashier was back on the product screen
            // (a WDF re-bill), and addPaymentline amounts the new line to
            // whatever is due NOW. Same method, same amount - invisible.
            for (const line of [...order.payment_ids]) {
                order.removePaymentline(line);
            }
            const result = order.addPaymentline(method);
            if (!result || !result.status) {
                console.warn("laundry_pos: on-account tender refused", result?.data);
            }
        } catch (e) {
            // Never break the payment screen over this - fall back to a manual
            // tender, which the lock above then leaves alone.
            console.warn("laundry_pos: on-account tender failed", e);
        }
    },

    _laundryAccountOnlyRefused() {
        const now = Date.now();
        if (now - (this._laundryAccountOnlyNotifiedAt || 0) < ACCOUNT_ONLY_NOTIFY_MS) {
            return;
        }
        this._laundryAccountOnlyNotifiedAt = now;
        this.laundryNotification.add(ACCOUNT_ONLY_MESSAGE, { type: "warning" });
    },

    // ---- Lock: block manual add / delete / amount edits on a locked refund. ----
    // The mirror above uses order-level methods, so it doesn't pass through these.
    addNewPaymentLine(paymentMethod) {
        if (this._laundryLockedPayments) {
            return false; // locked — the mirrored tender is already set
        }
        if (this._laundryAccountOnlyTender) {
            this._laundryAccountOnlyRefused();
            return false;
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
        if (this._laundryAccountOnlyTender) {
            this._laundryAccountOnlyRefused();
            return;
        }
        return super.deletePaymentLine(...arguments);
    },

    updateSelectedPaymentline(amount) {
        if (this._laundryLockedPayments) {
            return; // locked — amount is fixed to the original
        }
        // Fixed to the full amount due. Letting it be lowered would leave a
        // balance the cashier then cannot tender, since every other method is
        // refused above - a dead end, not a partial payment.
        if (this._laundryAccountOnlyTender) {
            this._laundryAccountOnlyRefused();
            return;
        }
        return super.updateSelectedPaymentline(...arguments);
    },
});
