/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ReceiptHeader } from "@point_of_sale/app/screens/receipt_screen/receipt/receipt_header/receipt_header";
import {
    laundryCodeForProduct,
    fmtDateTime12,
    fmtStoredDateTime,
} from "@laundry_pos/utils/laundry_products";
import { LAUNDRY_MENU } from "@laundry_pos/utils/laundry_instructions";
import { lsLoad } from "@laundry_pos/utils/laundry_storage";

const SERVICE_LABELS = {
    dropoff: "Drop-off",
    dropoff_delivery: "Drop-off & Delivery",
    pickup_delivery: "Pickup & Delivery",
    locker: "Locker",
    self_service: "Self-service",
};

patch(ReceiptHeader.prototype, {
    // Resolve laundry meta from the order (JS fields) or localStorage (reprints).
    _laundryData() {
        const order = this.props.order;
        if (!order) return null;
        let svcType = order.laundry_service_type;
        let custType = order.laundry_customer_type;
        let turnaround = order.laundry_turnaround;
        // `laundry_schedule` is an in-memory JS prop — it is gone after a reload,
        // a re-sync, or on another device, while laundry_service_type (a stored
        // field) always comes back. So this fallback must NOT be gated on svcType
        // alone, or the schedule can never be recovered. Last resort is the stored
        // laundry_*_datetime fields, in the getters below.
        let schedule = order.laundry_schedule || {};
        if (!svcType || !Object.keys(schedule).length) {
            const stored = lsLoad(order.uuid);
            if (stored?.status === "submitted") {
                svcType = svcType || stored.serviceType;
                custType = custType || stored.customerType;
                turnaround = turnaround || stored.turnaround;
                if (!Object.keys(schedule).length) {
                    schedule = stored.schedule || {};
                }
            }
        }
        if (!svcType) return null;
        return { order, svcType, custType, turnaround, schedule };
    },

    get laundryActive() {
        return this._laundryData() !== null;
    },

    get laundryCustomerType() {
        const d = this._laundryData();
        if (!d) return "";
        return d.custType === "new" ? "New" : d.custType === "returning" ? "Returning" : "—";
    },

    // Distinct selected services (array), derived from the laundry lines.
    get laundryServices() {
        const d = this._laundryData();
        if (!d) return [];
        const labelByCode = Object.fromEntries(LAUNDRY_MENU.map((m) => [m.code, m.label]));
        const codes = [];
        for (const l of d.order.lines || []) {
            const c = laundryCodeForProduct(l.product_id?.product_tmpl_id);
            if (c && !codes.includes(c)) codes.push(c);
        }
        return codes.map((c) => labelByCode[c] || c);
    },

    get laundryTAT() {
        const d = this._laundryData();
        if (!d) return "";
        return d.turnaround === "express" ? "Express"
             : d.turnaround === "regular" ? "Regular" : "—";
    },

    get laundryServiceType() {
        const d = this._laundryData();
        return d ? (SERVICE_LABELS[d.svcType] || "") : "";
    },

    // Customer tags are printed only for Locker orders.
    get laundryIsLocker() {
        return this._laundryData()?.svcType === "locker";
    },

    get laundryPickup() {
        const d = this._laundryData();
        if (!d) return "";
        return (
            fmtDateTime12(d.schedule.pickupDate, d.schedule.pickupHour) ||
            fmtStoredDateTime(d.order.laundry_pickup_datetime)
        );
    },

    get laundryDeliveryLabel() {
        const d = this._laundryData();
        return d && d.svcType === "dropoff" ? "Claim" : "Delivery";
    },

    get laundryDelivery() {
        const d = this._laundryData();
        if (!d) return "";
        if (d.schedule.deliveryDate) {
            return fmtDateTime12(d.schedule.deliveryDate, d.schedule.deliveryHour);
        }
        if (d.schedule.claimDate) {
            return fmtDateTime12(d.schedule.claimDate, d.schedule.claimHour);
        }
        return (
            fmtStoredDateTime(d.order.laundry_delivery_datetime) ||
            fmtStoredDateTime(d.order.laundry_claim_datetime)
        );
    },

    // Pickup rider who signed off on a Pickup & Delivery / Locker order (set at payment).
    get laundryRider() {
        return this.props.order?.laundry_pickup_rider || "";
    },
    // Delivery rider — captured by the delivery sign-off (process to follow).
    get laundryDeliveryRider() {
        return this.props.order?.laundry_delivery_rider || "";
    },
});
