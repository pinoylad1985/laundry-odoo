/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { partnerMatchesQuery, buildPartnerSearchDomain } from "@laundry_pos/utils/partner_search";
import { RefundGatePopup } from "@laundry_pos/refund_gate/refund_gate_popup";

/**
 * LIST hub action — adds a customer search bar above the order "Search Orders"
 * bar. Picking a customer's "All Orders" filters the order list by that exact
 * customer (reusing the built-in PARTNER + partnerId search). The two search
 * bars are mutually exclusive via `mode` ('' | 'customer' | 'order'): using one
 * greys out the other.
 */
patch(TicketScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = useService("dialog");
        this.orm = useService("orm");
        this.laundryState = useState({
            customerQuery: "",
            selectedCustomer: null,
            showCustomerResults: false,
            mode: "",
            rev: 0,
        });
    },

    /**
     * No PARTIAL refunds — a line is always refunded for its FULL remaining
     * quantity. We replace core's qty-from-buffer logic: any engagement snaps the
     * line to its full refundable qty; only an explicit 0 clears it. (Full override
     * of core `_setToRefundDetail`, not a super call — re-check on Odoo upgrades.)
     */
    _setToRefundDetail(toRefundDetail, buffer) {
        if (toRefundDetail.destionation_order_id) {
            return this.numberBuffer.reset();
        }
        toRefundDetail.refundableQty = toRefundDetail.line.qty - toRefundDetail.line.refundedQty;
        if (toRefundDetail.refundableQty <= 0) {
            return this.numberBuffer.reset();
        }
        toRefundDetail.qty = parseFloat(buffer) === 0 ? 0 : toRefundDetail.refundableQty;
    },

    /**
     * Click "Refund" → refund the WHOLE order: every line at its full remaining
     * quantity, with no per-line selection or quantity entry needed.
     */
    async onDoRefund() {
        const order = this.getSelectedOrder();
        if (!order) {
            return super.onDoRefund(...arguments);
        }
        // Refund control gate: a paid order can only be refunded against a valid rebooked
        // replacement order (same customer + later date) OR with a manager's approval.
        const approval = await this._laundryRefundGate(order);
        if (!approval) {
            return; // cancelled / not approved — abort the refund
        }
        // Capture the original's tender(s) so the refund is locked to the exact same
        // payment method(s) and amount(s), negated (see payment_screen_patch).
        let lockedPayments = [];
        try {
            lockedPayments = await this.orm.call("pos.order", "get_laundry_refund_payments", [order.id]);
        } catch {
            lockedPayments = [];
        }
        for (const line of order.getOrderlines()) {
            const detail = this.getToRefundDetail(line);
            if (detail.destionation_order_id) {
                continue; // already linked to a refund — leave it
            }
            const full = line.qty - line.refundedQty;
            detail.qty = full > 0 ? full : 0;
        }
        const result = await super.onDoRefund(...arguments);
        this._laundryStampRefund(approval);
        const refundOrder = this.pos.getOrder();
        if (refundOrder) {
            refundOrder._laundryLockedPayments = lockedPayments;
        }
        return result;
    },

    // Show the refund gate; resolves with the approval payload, or null if cancelled.
    _laundryRefundGate(order) {
        return new Promise((resolve) => {
            this.dialog.add(
                RefundGatePopup,
                { originalOrderId: order.id, onApproved: (payload) => resolve(payload) },
                { onClose: () => resolve(null) }
            );
        });
    },

    // Record the approval on the newly-created refund order (the active order now).
    _laundryStampRefund(approval) {
        const refundOrder = this.pos.getOrder();
        if (!refundOrder || !approval) {
            return;
        }
        if (approval.mode === "rebook") {
            refundOrder.laundry_refund_rebook_ref = approval.rebookRef;
        } else if (approval.mode === "override") {
            refundOrder.laundry_refund_manager = approval.manager;
            refundOrder.laundry_refund_reason = approval.reason;
        }
    },

    onLaundryCustomerInput(ev) {
        const val = ev.target.value;
        this.laundryState.customerQuery = val;
        this.laundryState.selectedCustomer = null;
        this.laundryState.showCustomerResults = !!val;
        this.laundryState.mode = val ? "customer" : "";
    },

    // Press Enter → also search the server (POS pre-loads only a subset of customers).
    onLaundryCustomerKeydown(ev) {
        if (ev.key === "Enter") {
            this._serverSearchCustomers();
        }
    },

    async _serverSearchCustomers() {
        const q = this.laundryState.customerQuery.trim();
        if (!q || this._searching) {
            return;
        }
        this._searching = true;
        try {
            await this.pos.data.callRelated("res.partner", "get_new_partner", [
                this.pos.config.id,
                buildPartnerSearchDomain(q),
                0,
            ]);
        } finally {
            this._searching = false;
            this.laundryState.rev++;
        }
    },

    get laundryCustomerResults() {
        void this.laundryState.rev; // re-run after a server search loads more customers
        const q = this.laundryState.customerQuery.trim();
        if (!q) {
            return [];
        }
        return (this.pos.models["res.partner"].getAll() || []).filter((p) => partnerMatchesQuery(p, q));
    },

    laundrySelectCustomer(partner) {
        this.laundryState.selectedCustomer = partner;
        this.laundryState.customerQuery = partner.name;
        this.laundryState.showCustomerResults = false;
        this.laundryState.mode = "customer";
        this.onSearch({ fieldName: "PARTNER", searchTerm: partner.name, partnerId: partner.id });
    },

    laundryClearCustomer() {
        this.laundryState.selectedCustomer = null;
        this.laundryState.customerQuery = "";
        this.laundryState.showCustomerResults = false;
        this.laundryState.mode = "";
        this.onSearch({ fieldName: "PARTNER", searchTerm: "" });
    },

    // Order search bar used (anything other than our customer-by-id filter)
    // → drop the customer selection so only one filter is ever active.
    onSearch(search) {
        if (!(search.fieldName === "PARTNER" && search.partnerId)) {
            this.laundryState.selectedCustomer = null;
            this.laundryState.customerQuery = "";
            this.laundryState.mode = search.searchTerm ? "order" : "";
        }
        return super.onSearch(search);
    },
});
