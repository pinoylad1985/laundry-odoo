/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { NewOrderModal } from "@laundry_pos/new_order_modal/new_order_modal";
import { SettleModal } from "@laundry_pos/settle_modal/settle_modal";
import { lsSave, lsLoad } from "@laundry_pos/utils/laundry_storage";
import {
    laundryCodeForProduct,
    fmtDateTime12,
    fmtStoredDateTime,
    withTatTurnaround,
    buildConfiguredLineVals,
} from "@laundry_pos/utils/laundry_products";
import { LAUNDRY_MENU } from "@laundry_pos/utils/laundry_instructions";
import { useState, useEffect, onMounted, onWillUnmount } from "@odoo/owl";

const SERVICE_LABELS = {
    dropoff: "Drop-off",
    dropoff_delivery: "Drop-off & Delivery",
    pickup_delivery: "Pickup & Delivery",
    locker: "Locker",
    self_service: "Self-service",
};

patch(ProductScreen.prototype, {
    setup() {
        super.setup();

        // mode: 'idle' | 'submitted' | 'skipped'
        this.laundryState = useState({ mode: "idle", flash: false, turnaround: null, modalOpen: false });

        // Sync banner mode when switching orders or returning to POS
        useEffect(
            () => {
                const order = this.pos.getOrder();
                const stored = lsLoad(order?.uuid);

                // Rehydrate JS-only fields from localStorage after a reload
                // (laundry_* props are not synced to the server, so they vanish).
                if (stored?.status === "submitted" && !order?.laundry_service_type && order) {
                    order.laundry_service_type  = stored.serviceType;
                    order.laundry_customer_type = stored.customerType;
                    order.laundry_schedule      = stored.schedule   || {};
                    order.laundry_turnaround    = stored.turnaround || null;
                }

                if (order?.laundry_service_type) {
                    this.laundryState.mode = "submitted";
                    this.laundryState.turnaround = stored?.turnaround || order?.laundry_turnaround || null;
                    if (order && !order._laundryPurpose) order._laundryPurpose = "sell";
                } else if (stored?.status === "skipped" || order?._laundrySetupProcessed) {
                    this.laundryState.mode = "skipped";
                    this.laundryState.turnaround = null;
                    if (order && !order._laundryPurpose) order._laundryPurpose = "sell";
                } else {
                    this.laundryState.mode = "idle";
                    this.laundryState.turnaround = null;
                }
            },
            () => [this.pos.getOrder()?.uuid]
        );

        // The setup modal is NOT auto-opened — the cashier opens it via the
        // "Set Up Now" button on the setup banner.

        // Listen for the flash signal (PosStore blocks the Customer button) and for
        // the navbar Settle button.
        onMounted(() => {
            this._laundryFlashHandler = () => this._flashBanner();
            document.addEventListener("laundry-flash-needed", this._laundryFlashHandler);

            this._laundryActionHandler = (ev) => this._runLaundryAction(ev.detail?.action);
            document.addEventListener("laundry-action", this._laundryActionHandler);
        });
        onWillUnmount(() => {
            document.removeEventListener("laundry-flash-needed", this._laundryFlashHandler);
            document.removeEventListener("laundry-action", this._laundryActionHandler);
        });
    },

    _flashBanner() {
        this.laundryState.flash = true;
        setTimeout(() => { this.laundryState.flash = false; }, 600);
    },

    // ── Settle (navbar Settle button) ─────────────────────────────────────

    async _runLaundryAction(action) {
        if (action === "new_order") {
            // Open the setup modal for the CURRENT order (same path as the banner's
            // "Set Up Now"). We deliberately do NOT create/navigate to a new order —
            // that navigation fails under the POS service worker. Start the next
            // order via the native ＋ instead.
            if (!this.pos.getOrder()) {
                this.pos.addNewOrder();
            }
            const order = this.pos.getOrder();
            // Commit this order to a SALE the instant New Order is clicked, so Settle
            // disables immediately (mutually exclusive) — not only once completed.
            if (order) order._laundryPurpose = "sell";
            return this._showLaundrySetupModal(order, false);
        }
        if (action === "settle") {
            const order = this.pos.getOrder();
            if (order) order._laundryPurpose = "settle"; // disables New Order immediately
            return this._openSettleModal();
        }
    },

    _openSettleModal() {
        this.dialog.add(SettleModal, {});
    },

    // True when the current order is a settlement (deposit / settle orders or
    // invoices) — those use the cart, so the product-grid lock must not show.
    _laundryIsSettlement() {
        const order = this.pos.getOrder();
        if (!order) return false;
        if (order.is_settling_account) return true;
        return (order.lines || []).some((l) => l.settled_order_id || l.settled_invoice_id);
    },

    _getLaundryServiceLabel() {
        const order = this.pos.getOrder();
        return SERVICE_LABELS[order?.laundry_service_type] || "";
    },

    _getLaundryCustomerLabel() {
        const order = this.pos.getOrder();
        if (order?.partner_id?.name) return order.partner_id.name;
        if (order?.laundry_customer_type === "new") return "New Customer";
        if (order?.laundry_customer_type === "returning") return "Returning Customer";
        return "";
    },

    _getLaundryTurnaroundLabel() {
        const t = this.laundryState.turnaround;
        if (t === "express") return "⚡ Express";
        if (t === "regular") return "🕐 Regular";
        return "";
    },

    _getLaundryTurnaroundType() {
        return this.laundryState.turnaround || "";
    },

    // ── Banner detail getters ─────────────────────────────────────────────

    _getLaundryCustomerTypeLabel() {
        const t = this.pos.getOrder()?.laundry_customer_type;
        if (t === "new") return "New";
        if (t === "returning") return "Returning";
        return "—";
    },

    // Distinct selected services, derived from the laundry lines on the order.
    _getLaundryServices() {
        const order = this.pos.getOrder();
        const labelByCode = Object.fromEntries(LAUNDRY_MENU.map((m) => [m.code, m.label]));
        const codes = [];
        for (const l of order?.lines || []) {
            const code = laundryCodeForProduct(l.product_id?.product_tmpl_id);
            if (code && !codes.includes(code)) codes.push(code);
        }
        return codes.map((c) => labelByCode[c] || c).join(", ");
    },

    // Turnaround as plain text (Express / Regular).
    _getLaundryTAT() {
        const t = this.laundryState.turnaround;
        if (t === "express") return "Express";
        if (t === "regular") return "Regular";
        return "—";
    },

    // In-memory schedule if present, else whatever was stored for this order on
    // THIS browser (the in-memory prop does not survive a reload / re-sync).
    _getLaundrySchedule() {
        const order = this.pos.getOrder();
        const schedule = order?.laundry_schedule || {};
        if (Object.keys(schedule).length) return schedule;
        const stored = lsLoad(order?.uuid);
        return (stored?.status === "submitted" && stored.schedule) || {};
    },

    _fmtDateTime(date, hour) {
        return fmtDateTime12(date, hour);
    },

    _getLaundryPickup() {
        const s = this._getLaundrySchedule();
        return (
            this._fmtDateTime(s.pickupDate, s.pickupHour) ||
            fmtStoredDateTime(this.pos.getOrder()?.laundry_pickup_datetime)
        );
    },

    // Drop-off uses a Claim date; the other service types use a Delivery date.
    _getLaundryDeliveryLabel() {
        return this.pos.getOrder()?.laundry_service_type === "dropoff" ? "Claim" : "Delivery";
    },

    _getLaundryDelivery() {
        const s = this._getLaundrySchedule();
        if (s.deliveryDate) return this._fmtDateTime(s.deliveryDate, s.deliveryHour);
        if (s.claimDate) return this._fmtDateTime(s.claimDate, s.claimHour);
        const order = this.pos.getOrder();
        return (
            fmtStoredDateTime(order?.laundry_delivery_datetime) ||
            fmtStoredDateTime(order?.laundry_claim_datetime)
        );
    },

    // Push the order's current TAT onto already-configured laundry lines so a
    // schedule change re-syncs each product's turnaround (remove + re-add).
    async _reapplyTatToLaundryLines(order) {
        const tat = order?.laundry_turnaround;
        if (!tat || !order?.lines) return;
        for (const line of [...order.lines]) {
            const tmpl = line.product_id?.product_tmpl_id;
            if (!laundryCodeForProduct(tmpl)) continue;
            const hasTurn = (line.attribute_value_ids || []).some(
                (v) => String(v.attribute_id?.name || "").startsWith("Turnaround")
            );
            if (!hasTurn) continue; // not configured yet — TAT is applied on configure
            const current = (line.attribute_value_ids || []).map((v) => v.id);
            const updated = withTatTurnaround(tmpl, current, tat);
            const unchanged =
                updated.length === current.length && updated.every((id) => current.includes(id));
            if (unchanged) continue;
            const vals = buildConfiguredLineVals(this.pos, tmpl, updated);
            if (!vals.product_id) vals.product_id = line.product_id;
            vals.qty = line.qty; // preserve quantity
            if (typeof order.removeOrderline === "function") order.removeOrderline(line);
            else if (typeof line.delete === "function") line.delete();
            await this.pos.addLineToCurrentOrder(vals, {}, false);
        }
    },

    /**
     * @param {object} order
     * @param {boolean} isChange - when true, skipping keeps the existing setup intact
     */
    async _showLaundrySetupModal(order, isChange) {
        if (order) order._laundrySetupProcessed = true;

        // Pre-populate modal with any previously saved details — whether the
        // order was fully submitted OR skipped with partial selections.
        const stored = lsLoad(order?.uuid);
        const initData = (stored && (stored.serviceType || stored.customerType)) ? stored : null;

        // While the setup modal is open, suppress the setup banner above the cart.
        this.laundryState.modalOpen = true;
        let result;
        try {
            result = await makeAwaitable(this.dialog, NewOrderModal, {
                initialData: initData || undefined,
            });
        } finally {
            this.laundryState.modalOpen = false;
        }

        // Explicit "Skip for now" — save whatever was selected for later editing
        if (result?.skipped) {
            this.laundryState.mode = "skipped";
            this.laundryState.turnaround = null;
            lsSave(order?.uuid, {
                status:       "skipped",
                serviceType:  result.serviceType  || null,
                customerType: result.customerType || null,
                schedule:     result.schedule     || {},
                turnaround:   result.turnaround   || null,
            });
            // Keep any customer the cashier already picked
            if (result.partner) {
                this.pos.setPartnerToCurrentOrder(result.partner);
            }
            return;
        }

        // Closed via X / escape (no payload) — keep existing setup on Change,
        // otherwise mark as skipped with nothing saved.
        if (!result) {
            if (!isChange) {
                this.laundryState.mode = "skipped";
                this.laundryState.turnaround = null;
                lsSave(order?.uuid, { status: "skipped" });
            }
            return;
        }

        this.laundryState.mode = "submitted";
        this.laundryState.turnaround = result.turnaround;

        order.laundry_service_type  = result.serviceType;
        order.laundry_customer_type = result.customerType;
        order.laundry_schedule      = result.schedule   || {};
        order.laundry_turnaround    = result.turnaround || null;

        // Persist the order details as real pos.order fields (reportable in the
        // backend). Cashier Due / Phone / Address are computed server-side.
        const { DateTime } = luxon;
        const toDT = (d, h) => (d ? DateTime.fromISO(`${d}T${h || "00:00"}:00`) : false);
        const sched = result.schedule || {};
        order.laundry_services          = this._getLaundryServices();
        order.laundry_claim_datetime    = toDT(sched.claimDate, sched.claimHour);
        order.laundry_pickup_datetime   = toDT(sched.pickupDate, sched.pickupHour);
        order.laundry_delivery_datetime = toDT(sched.deliveryDate, sched.deliveryHour);

        // The TAT may have changed (e.g. new schedule via Change) — push it onto
        // the already-configured laundry lines so their turnaround stays in sync.
        await this._reapplyTatToLaundryLines(order);

        // Products were already added to the order by the service pills; just
        // persist the meta so it survives reload and pre-populates the Change modal.
        lsSave(order?.uuid, {
            status:       "submitted",
            serviceType:  result.serviceType,
            customerType: result.customerType,
            schedule:     result.schedule   || {},
            turnaround:   result.turnaround || null,
        });

        if (result.editPartner) {
            await this.pos.selectPartner(order);
        } else if (result.partner) {
            this.pos.setPartnerToCurrentOrder(result.partner);
        }
    },

    // Flash the banner instead of adding the product when setup is skipped
    addProductToOrder(product) {
        const order = this.pos.getOrder();
        const stored = lsLoad(order?.uuid);
        if (
            (order?._laundrySetupProcessed || stored?.status === "skipped") &&
            !order?.laundry_service_type
        ) {
            this._flashBanner();
            return;
        }
        return super.addProductToOrder(product);
    },
});
