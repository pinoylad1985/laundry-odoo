/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { LAUNDRY_MENU, LONG_SERVICE_CODES } from "@laundry_pos/utils/laundry_instructions";
import { findLaundryProduct, laundryCodeForProduct } from "@laundry_pos/utils/laundry_products";
import { partnerMatchesQuery, buildPartnerSearchDomain } from "@laundry_pos/utils/partner_search";
import { DateWheel, fmtDateLabel } from "@laundry_pos/new_order_modal/date_wheel";

// Buttons are styled like the POS numpad (core's `.numpad-button`): every unselected
// key is plain grey and the SELECTED one goes solid primary, so colour carries one
// meaning only. The +/− steppers are the exception — red/green there is the control,
// not decoration.
const CUSTOMER_TYPES = [
    { code: "new",       label: "New Customer" },
    { code: "returning", label: "Returning Customer" },
];

// Order matters: the template lays these out in 2 columns filled DOWN, so this is
// column 1 (drop-off, drop-off & delivery, self-service) then column 2 (pickup &
// delivery, locker) — the store-drop-off flows first, the come-to-you flows second.
const SERVICE_TYPES = [
    { code: "dropoff",           label: "Drop-off" },
    { code: "dropoff_delivery",  label: "Drop-off & Delivery" },
    { code: "self_service",      label: "Self-service" },
    { code: "pickup_delivery",   label: "Pickup & Delivery" },
    { code: "locker",            label: "Locker" },
];

export class NewOrderModal extends Component {
    static template = "laundry_pos.NewOrderModal";
    static components = { Dialog, DateWheel };
    static props = {
        getPayload: Function,
        close: Function,
        initialData: { type: Object, optional: true },
    };

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        this.state = useState({
            // Step 1 — Customer
            customerType: null,
            partnerQuery: "",
            selectedPartner: null,
            // Step 2 — Services: pressing a pill adds the product to the REAL
            // POS order. `rev` is bumped on each press to recompute pill state.
            rev: 0,
            // Step 3 — Service Type
            serviceType: null,
            // Step 4 — Schedule (flat keys to keep OWL reactivity simple)
            claimDate: "",    claimHour: "",
            deliveryDate: "", deliveryHour: "",
            pickupDate: "",   pickupHour: "",
            pdDelDate: "",    pdDelHour: "",
        });
        this.customerTypes = CUSTOMER_TYPES;
        this.serviceTypes = SERVICE_TYPES;
        this.services = LAUNDRY_MENU; // { code, label } pills

        // Hour pills: all 24 in order. The template lays them out as 4 columns of 6
        // (grid-auto-flow: column), so they read 12 AM–5 AM / 6–11 AM / 12–5 PM / 6–11 PM.
        const label = (h) => {
            const ampm = h < 12 ? "AM" : "PM";
            const disp = h % 12 === 0 ? 12 : h % 12;
            return `${disp} ${ampm}`;
        };
        this.hours = Array.from({ length: 24 }, (_, h) => ({
            h,
            value: String(h).padStart(2, "0") + ":00",
            label: label(h),
        }));

        // Pre-populate from previously submitted details (Change / after reload)
        this._applyInitialData(this.props.initialData);

        // The order's customer is the single source of truth: if one is already set
        // (via the Control Button or a previous setup), auto-select it here.
        const orderPartner = this.pos.getOrder()?.getPartner?.();
        if (orderPartner) {
            this.state.customerType = "returning";
            this.state.selectedPartner = orderPartner;
        }
    }

    // Restore saved details into form state so the cashier can edit them
    _applyInitialData(data) {
        if (!data) return;
        const s = this.state;
        s.customerType = data.customerType || null;
        s.serviceType  = data.serviceType  || null;

        // Schedule — note storage uses deliveryDate/deliveryHour even for
        // pickup_delivery, which map to pdDelDate/pdDelHour in state.
        const sched = data.schedule || {};
        const st = data.serviceType;
        if (st === "dropoff") {
            s.claimDate = sched.claimDate || "";
            s.claimHour = sched.claimHour || "";
        } else if (st === "dropoff_delivery") {
            s.deliveryDate = sched.deliveryDate || "";
            s.deliveryHour = sched.deliveryHour || "";
        } else if (st === "pickup_delivery" || st === "locker") {
            s.pickupDate = sched.pickupDate || "";
            s.pickupHour = sched.pickupHour || "";
            s.pdDelDate  = sched.deliveryDate || "";
            s.pdDelHour  = sched.deliveryHour || "";
        }
    }

    // ── Step 1: Customer ──────────────────────────────────────────────────

    selectCustomerType(type) {
        this.state.customerType = type;
        this.state.partnerQuery = "";
        this.state.selectedPartner = null;
    }

    onSearchInput(ev) {
        this.state.partnerQuery = ev.target.value;
        this.state.selectedPartner = null;
    }

    // Press Enter → also search the SERVER. The POS only pre-loads a subset of
    // customers, so a name like "Val" may not be loaded; this fetches matches.
    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            this._serverSearchPartners();
        }
    }

    async _serverSearchPartners() {
        const q = this.state.partnerQuery.trim();
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
            this.state.rev++; // re-run filteredPartners with the newly loaded customers
        }
    }

    pickPartner(partner) {
        this.state.selectedPartner = partner;
        this.state.partnerQuery = "";
        this.pos.setPartnerToCurrentOrder(partner); // share with the Control Button / order
    }

    unselectPartner() {
        this.state.selectedPartner = null;
        this.state.partnerQuery = "";
        this.pos.setPartnerToCurrentOrder(false); // clear it on the order too
    }

    editPartner(partner) {
        this.props.getPayload({
            customerType: this.state.customerType,
            serviceType: this.state.serviceType,
            partner: null,
            editPartner: partner,
            schedule: this._getSchedule(),
            turnaround: this.turnaroundType,
        });
        this.props.close();
    }

    get filteredPartners() {
        void this.state.rev; // re-run after a server search loads more customers
        const query = this.state.partnerQuery.trim();
        if (!query) return [];
        const all = this.pos.models["res.partner"]?.getAll() ?? [];
        return all.filter((p) => partnerMatchesQuery(p, query));
    }

    get showNoResults() {
        return (
            !this.state.selectedPartner &&
            this.state.partnerQuery.trim().length > 0 &&
            this.filteredPartners.length === 0
        );
    }

    partnerAddress(partner) {
        return [partner.street, partner.street2, partner.city]
            .map((v) => String(v || "").trim())
            .filter(Boolean)
            .join(", ");
    }

    partnerTags(partner) {
        // pos_tag_names is a comma-joined string computed server-side, so it is
        // available for both pre-loaded and on-demand partners (unlike the
        // category_id relation, whose category records aren't all loaded).
        const names = String(partner.pos_tag_names || "");
        return names ? names.split(",").map((s) => s.trim()).filter(Boolean) : [];
    }

    // ── Step 2: Services (pills add unconfigured products to the order) ────

    // Pressing a pill adds the matching product to the REAL POS order as a new
    // unconfigured line (variants/attributes are chosen later in the cart).
    addService(code) {
        if (this.servicesDisabled) return;
        const product = findLaundryProduct(this.pos, code);
        if (!product) return;
        this.pos.addLineToCurrentOrder({ product_tmpl_id: product }, {}, false);
        this.state.rev++; // recompute pill highlight + turnaround
    }

    // Remove the most recently added line of this service (the "−" button).
    // Lines never merge, so one line == one press == one count.
    removeService(code) {
        const lines = this._orderLaundryLines().filter(
            (l) => laundryCodeForProduct(l.product_id?.product_tmpl_id) === code
        );
        if (!lines.length) return;
        const line = lines[lines.length - 1];
        const order = this.pos.getOrder();
        if (typeof order?.removeOrderline === "function") order.removeOrderline(line);
        else if (typeof line?.delete === "function") line.delete();
        this.state.rev++;
    }

    // How many lines of this service are on the order (drives the stepper count).
    serviceCount(code) {
        return this._orderLaundryLines().filter(
            (l) => laundryCodeForProduct(l.product_id?.product_tmpl_id) === code
        ).length;
    }

    // Laundry lines currently on the order (rev makes this reactive to presses).
    _orderLaundryLines() {
        void this.state.rev; // reactive dependency
        const order = this.pos.getOrder();
        return (order?.lines || []).filter((l) =>
            laundryCodeForProduct(l.product_id?.product_tmpl_id)
        );
    }

    // Pill is highlighted while the order holds >=1 line of that product.
    isServiceSelected(code) {
        return this._orderLaundryLines().some(
            (l) => laundryCodeForProduct(l.product_id?.product_tmpl_id) === code
        );
    }

    get hasAnyService() {
        return this._orderLaundryLines().length > 0;
    }

    // true when Dry/Wet Clean or Shoe Clean is on the order (longer turnaround)
    get hasLongService() {
        return this._orderLaundryLines().some((l) =>
            LONG_SERVICE_CODES.includes(laundryCodeForProduct(l.product_id?.product_tmpl_id))
        );
    }

    // ── Step 3: Service Type ──────────────────────────────────────────────

    selectServiceType(code) {
        // Self-service needs no products — drop any service lines already added.
        if (code === "self_service") {
            this._clearLaundryServiceLines();
        }
        this.state.serviceType = code;
        this._resetSchedule();
    }

    // Remove every laundry service line from the order (used when switching to self-service).
    _clearLaundryServiceLines() {
        const order = this.pos.getOrder();
        for (const line of this._orderLaundryLines()) {
            if (typeof order?.removeOrderline === "function") order.removeOrderline(line);
            else if (typeof line?.delete === "function") line.delete();
        }
        this.state.rev++;
    }

    _resetSchedule() {
        this.state.claimDate = "";    this.state.claimHour = "";
        this.state.deliveryDate = ""; this.state.deliveryHour = "";
        this.state.pickupDate = "";   this.state.pickupHour = "";
        this.state.pdDelDate = "";    this.state.pdDelHour = "";
    }

    // ── Step 4: Schedule helpers ──────────────────────────────────────────

    // The hour picker is two blocks with a gutter between them, so a morning slot is
    // never one mis-tap away from the same hour in the afternoon.
    get amHours() {
        return this.hours.slice(0, 12);
    }

    get pmHours() {
        return this.hours.slice(12);
    }

    // Today and tomorrow only — everything further out is the DateWheel next to them.
    // Each key reads as the DATE first ("Sep 21") with the shortcut under it, so the
    // cashier confirms against the date rather than against a word.
    get quickDates() {
        return [0, 1].map((offset) => {
            const d = new Date();
            d.setDate(d.getDate() + offset);
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, "0");
            const day = String(d.getDate()).padStart(2, "0");
            const value = `${y}-${m}-${day}`;
            return { value, label: fmtDateLabel(value), note: offset === 0 ? "Today" : "Tomorrow" };
        });
    }

    setDate(field, value) {
        this.state[field] = value;
    }

    setHour(field, value) {
        this.state[field] = value;
    }

    /**
     * Whether an hour pill is unavailable for a given selector.
     * @param {string} kind - 'claim' | 'pickup' | 'delivery'
     * @param {number} h - hour 0..23
     */
    isHourDisabled(kind, h, dateVal) {
        // Past dates/times are allowed (no grey-out); only closed hours below are blocked.
        // Claim — the Drop-off flow's only time — is open around the clock: the customer
        // collects at the counter, so there is no route to schedule around.
        if (kind === "claim") return false;
        // 4 AM–5 AM closed for every other selector.
        if (h === 4 || h === 5) return true;
        // Pickup / Delivery also close 2 AM–3 AM.
        if (kind === "pickup" || kind === "delivery") return h === 2 || h === 3;
        return false;
    }

    // Self-service needs no laundry products, so the service pills are disabled for it.
    get servicesDisabled() {
        return this.state.serviceType === "self_service";
    }

    // ── Turnaround calculation (mirrors pos.html logic) ───────────────────

    get turnaroundThreshold() {
        const long = this.hasLongService;
        const ret  = this.state.customerType === "returning";
        return long ? (ret ? 48 : 72) : (ret ? 18 : 24);
    }

    get turnaroundMinHrs() {
        return this.hasLongService ? 24 : 6;
    }

    _ms(dateVal, hourVal) {
        if (!dateVal || !hourVal) return null;
        return new Date(`${dateVal}T${hourVal}:00`).getTime();
    }

    get diffHours() {
        const s  = this.state;
        const st = s.serviceType;
        if (st === "pickup_delivery" || st === "locker") {
            const pu  = this._ms(s.pickupDate, s.pickupHour);
            const del = this._ms(s.pdDelDate, s.pdDelHour);
            if (pu && del) return Math.round((del - pu) / 3_600_000);
        } else if (st === "dropoff") {
            const claim = this._ms(s.claimDate, s.claimHour);
            if (claim) return Math.round((claim - Date.now()) / 3_600_000);
        } else if (st === "dropoff_delivery") {
            const del = this._ms(s.deliveryDate, s.deliveryHour);
            if (del) return Math.round((del - Date.now()) / 3_600_000);
        }
        return null;
    }

    get turnaroundType() {
        const diff = this.diffHours;
        if (diff === null) return null;
        return diff < this.turnaroundThreshold ? "express" : "regular";
    }

    get turnaroundError() {
        const diff = this.diffHours;
        if (diff === null || diff >= this.turnaroundMinHrs) return null;
        const who = this.hasLongService
            ? "Shoe Clean / Dry-Wet Cleaning"
            : "WDF / Press";
        return `${who} requires at least ${this._fmt(this.turnaroundMinHrs)}`;
    }

    _fmt(hrs) {
        if (hrs < 24) return `${hrs} hrs`;
        const d = hrs / 24;
        return `${d} day${d > 1 ? "s" : ""}`;
    }

    // Express: "6 hrs – under 24 hrs"
    get expressLabel() {
        return `${this._fmt(this.turnaroundMinHrs)} – under ${this._fmt(this.turnaroundThreshold)}`;
    }

    // Regular: "at least 24 hrs"
    get regularLabel() {
        return `at least ${this._fmt(this.turnaroundThreshold)}`;
    }

    // ── Validation ────────────────────────────────────────────────────────

    get scheduleComplete() {
        const s  = this.state;
        const st = s.serviceType;
        if (!st || st === "self_service") return true;
        if (st === "dropoff")          return !!(s.claimDate && s.claimHour);
        if (st === "dropoff_delivery") return !!(s.deliveryDate && s.deliveryHour);
        // pickup_delivery or locker
        return !!(s.pickupDate && s.pickupHour && s.pdDelDate && s.pdDelHour);
    }

    get canConfirm() {
        // Variant/attribute selection happens later in the main POS cart and is
        // enforced at payment, so it is not required to confirm the modal.
        // Self-service does not require a service to be selected.
        const isSelfService = this.state.serviceType === "self_service";
        // A Returning customer MUST have a partner actually selected (even after a
        // Change that cleared it); a New customer doesn't (created later / walk-in).
        const customerOk =
            this.state.customerType === "new" ||
            (this.state.customerType === "returning" && !!this.state.selectedPartner);
        return !!(
            customerOk &&
            (isSelfService || this.hasAnyService) &&
            this.state.serviceType &&
            this.scheduleComplete &&
            !this.turnaroundError
        );
    }

    // ── Payload helpers ───────────────────────────────────────────────────

    _getSchedule() {
        const s  = this.state;
        const st = s.serviceType;
        if (!st || st === "self_service") return {};
        if (st === "dropoff")
            return { claimDate: s.claimDate, claimHour: s.claimHour };
        if (st === "dropoff_delivery")
            return { deliveryDate: s.deliveryDate, deliveryHour: s.deliveryHour };
        return {
            pickupDate: s.pickupDate,   pickupHour: s.pickupHour,
            deliveryDate: s.pdDelDate,  deliveryHour: s.pdDelHour,
        };
    }

    confirm() {
        if (!this.canConfirm) return;
        this.props.getPayload({
            customerType: this.state.customerType,
            serviceType:  this.state.serviceType,
            partner:      this.state.selectedPartner || null,
            editPartner:  null,
            schedule:     this._getSchedule(),
            turnaround:   this.turnaroundType,
        });
        this.props.close();
    }

    skip() {
        // Save whatever was selected so far so it can be resumed/edited later
        this.props.getPayload({
            skipped:      true,
            customerType: this.state.customerType,
            serviceType:  this.state.serviceType,
            partner:      this.state.selectedPartner || null,
            schedule:     this._getSchedule(),
            turnaround:   this.turnaroundType,
        });
        this.props.close();
    }
}
