/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { lsDelete, lsMarkPrinted, lsWasPrinted } from "@laundry_pos/utils/laundry_storage";
import { lineNeedsConfig, laundryCodeForProduct, wdfBilledQty } from "@laundry_pos/utils/laundry_products";
import {
    computeLaundryCopies,
    laundryReprintOptions,
    setPrintOnlyCopy,
} from "@laundry_pos/overrides/order_receipt_patch";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { ReprintCopiesPopup } from "@laundry_pos/reprint_picker/reprint_copies_popup";
import { allowWdfQty } from "@laundry_pos/overrides/pos_order_line_patch";
import { consumeWdfWeight, consumeLaundryNote } from "@laundry_pos/overrides/product_configurator_popup_patch";
import { RiderSignoffPopup } from "@laundry_pos/rider_signoff/rider_signoff_popup";

patch(PosStore.prototype, {
    /**
     * Flag the newly created order so ProductScreen can show the laundry
     * setup modal after navigation completes.
     * NOTE: We do NOT make this async — doing so breaks the URL routing
     * (order.uuid becomes undefined before navigation fires).
     */
    addNewOrder(data = {}) {
        const order = super.addNewOrder(data);
        if (order) {
            order._needsLaundrySetup = true;
        }
        return order;
    },

    /**
     * Creating/configuring a line is a legitimate path for setting a Wash-Dry-Fold
     * quantity (the numpad is not — see pos_order_line_patch). Run line creation
     * inside the guard so WDF qty in `vals` is honored.
     */
    async addLineToCurrentOrder() {
        const line = await allowWdfQty(() => super.addLineToCurrentOrder(...arguments));
        // The CORE configure-flow (auto-opened when a WDF is added from the product
        // grid) ignores our custom weight payload. Apply the stashed weight to the
        // just-created line, then re-bill every WDF line for the new count.
        const weight = consumeWdfWeight();
        const target = line || this.getOrder()?.getSelectedOrderline?.();
        if (weight && target && laundryCodeForProduct(target.product_id?.product_tmpl_id) === "wdf") {
            target.laundry_actual_weight = weight;
            const wdfLines = (this.getOrder()?.lines || []).filter(
                (l) => laundryCodeForProduct(l.product_id?.product_tmpl_id) === "wdf"
            );
            allowWdfQty(() => {
                for (const l of wdfLines) {
                    if (l.laundry_actual_weight) {
                        l.setQuantity(wdfBilledQty(l.laundry_actual_weight, wdfLines.length));
                    }
                }
            });
        }
        // Apply the stashed customer note (from the configurator) to the just-created line.
        const note = consumeLaundryNote();
        if (note != null && target && typeof target.setCustomerNote === "function") {
            target.setCustomerNote(note);
        }
        return line;
    },

    /**
     * Block the Customer button when laundry setup was skipped.
     * Fires a DOM event so ProductScreen can flash the banner.
     * _needsLaundrySetup is explicitly set to false (not undefined) only after
     * the modal is shown, so existing orders without a service type are unaffected.
     */
    selectPartner(currentOrder = this.getOrder()) {
        if (currentOrder?._needsLaundrySetup === false && !currentOrder?.laundry_service_type) {
            document.dispatchEvent(new CustomEvent("laundry-flash-needed"));
            return false;
        }
        return super.selectPartner(currentOrder);
    },

    /**
     * Keep the Order List (TicketScreen) independent of the Customer control
     * button. The core seeds the order search with the current order's partner
     * name, which auto-filters the list by whoever is on the order. We always
     * open the Order List unfiltered so the two selections stay independent.
     */
    getDefaultSearchDetails() {
        return { fieldName: "RECEIPT_NUMBER", searchTerm: "" };
    },

    /**
     * When an order is cancelled/deleted, drop its saved laundry details so
     * stale data can't resurface if a future order reuses the same UUID.
     * removeOrder is the foundational single-order removal that all
     * deletion/cancellation flows funnel through.
     */
    removeOrder(order, removeFromServer = true) {
        lsDelete(order?.uuid);
        return super.removeOrder(order, removeFromServer);
    },

    /**
     * Print one job per laundry copy (TRANSACTION/SHOP/CUSTOMER) so the thermal
     * printer cuts between each. Non-laundry orders print once, normally.
     *
     * The FIRST print (straight after payment) prints the full set automatically —
     * no extra tap at checkout. Any print after that is a REPRINT and opens the
     * copy picker, so reprinting just the shop copy doesn't spit out all of them.
     *
     * "Already printed" is the ONLY reprint signal. Don't reintroduce `opts.order`
     * as one: core passes the order on the post-payment print too, so it flagged
     * every first print as a reprint. The flag is persisted (not just the
     * in-memory `_laundryPrinted` prop) so it survives a reload and a later
     * session's Order List reprint.
     */
    async printReceipt(opts = {}) {
        const order = opts.order || this.getOrder();
        let copies = computeLaundryCopies(order);
        if (!(copies.length && copies[0]?.label)) {
            return super.printReceipt(opts); // not a laundry order — single receipt
        }
        if (order?._laundryPrinted || lsWasPrinted(order?.uuid)) {
            const dialog = this.dialog || this.env?.services?.dialog;
            const chosen = await makeAwaitable(dialog, ReprintCopiesPopup, {
                copies: laundryReprintOptions(order),
            });
            if (!chosen?.length) {
                return; // cancelled, or nothing selected
            }
            copies = chosen;
        }
        let result;
        for (const copy of copies) {
            setPrintOnlyCopy(copy);
            try {
                result = await super.printReceipt({ ...opts, order });
            } finally {
                setPrintOnlyCopy(null);
            }
        }
        if (order) {
            order._laundryPrinted = true;
            lsMarkPrinted(order.uuid);
        }
        return result;
    },

    // Every tap creates a NEW line — products never merge into an existing line
    // (consistent for all products). Quantity is changed via the numpad instead.
    tryMergeOrderline(order, line, merge, selectedOrderline) {
        return super.tryMergeOrderline(order, line, false, selectedOrderline);
    },

    /**
     * Block going to payment while any laundry product line still has its
     * variants/attributes unselected. Both Pay buttons funnel through pos.pay().
     */
    async pay() {
        const order = this.getOrder();
        const missing = (order?.lines || []).filter((l) => lineNeedsConfig(l));
        if (missing.length) {
            const names = [
                ...new Set(
                    missing.map((l) => l.product_id?.product_tmpl_id?.name).filter(Boolean)
                ),
            ].join(", ");
            const dialog = this.dialog || this.env?.services?.dialog;
            dialog?.add(AlertDialog, {
                title: "Select product options",
                body: `Please choose options for these products before payment: ${names}`,
            });
            return;
        }

        // Wash-Dry-Fold is charged per KG: at least 6KG for a single WDF line,
        // or 4KG on each line when there is more than one WDF line.
        const wdfLines = (order?.lines || []).filter(
            (l) => laundryCodeForProduct(l.product_id?.product_tmpl_id) === "wdf"
        );
        if (wdfLines.length) {
            // Billed qty for a WDF line = its actual weight rounded to a whole kg (decimal
            // above 0.40 rounds up), but never below the current minimum (6KG single / 4KG
            // when 2+). This bumps short lines UP and brings a previously force-bumped line
            // back DOWN when the minimum changes (a WDF line added/removed flips the min).
            const billedQty = (l) => wdfBilledQty(l.laundry_actual_weight, wdfLines.length);
            const wrong = wdfLines.filter((l) => Math.abs((l.qty || 0) - billedQty(l)) > 0.001);
            if (wrong.length) {
                const dialog = this.dialog || this.env?.services?.dialog;
                dialog?.add(ConfirmationDialog, {
                    title: "Minimum Wash-Dry-Fold weight",
                    body:
                        wdfLines.length === 1
                            ? "Wash-Dry-Fold has a 6KG minimum. Click here to set the billed weight."
                            : "Wash-Dry-Fold has a 4KG minimum per line. Click here to set the billed weights.",
                    confirmLabel: "Click here",
                    confirm: () => {
                        // Set each line's BILLED qty to satisfy the current minimum;
                        // leave the entered Actual Weight untouched. Don't auto-proceed —
                        // the cashier reviews and clicks Pay again.
                        allowWdfQty(() => {
                            for (const l of wdfLines) {
                                l.setQuantity(billedQty(l));
                            }
                        });
                    },
                });
                return;
            }
        }

        // Pickup & Delivery / Locker orders require a rider sign-off (PIN) before payment.
        // `_riderSignedOff` is in-memory, so a reload between sign-off and payment would
        // ask for the PIN again; the rider NAME is a stored field, so treat it as proof
        // the sign-off already happened.
        const svc = order?.laundry_service_type;
        const signedOff = order?._riderSignedOff || !!order?.laundry_pickup_rider;
        if (["pickup_delivery", "locker"].includes(svc) && !signedOff) {
            const dialog = this.dialog || this.env?.services?.dialog;
            const payArgs = arguments;
            dialog?.add(RiderSignoffPopup, {
                onSignedOff: (riderName) => {
                    order._riderSignedOff = true;
                    order.laundry_pickup_rider = riderName;
                    this.pay(...payArgs); // re-enter; now signed off -> proceeds to payment
                },
            });
            return;
        }

        return super.pay(...arguments);
    },
});
