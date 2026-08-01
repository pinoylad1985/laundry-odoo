/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { onMounted } from "@odoo/owl";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

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
patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
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
        return super.addNewPaymentLine(...arguments);
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
