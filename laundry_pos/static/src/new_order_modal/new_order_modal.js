/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { LAUNDRY_MENU, LONG_SERVICE_CODES } from "@laundry_pos/utils/laundry_instructions";
import { findLaundryProduct, laundryCodeForProduct } from "@laundry_pos/utils/laundry_products";
import { partnerMatchesQuery, buildPartnerSearchDomain } from "@laundry_pos/utils/partner_search";
import { DateWheel, fmtDateLabel } from "@laundry_pos/new_order_modal/date_wheel";

// Buttons are styled like the POS numpad (core's `.numpad-button`): every unselected
// key is plain grey and the SELECTED one goes solid primary, so colour carries one
// meaning only. The +/− steppers are the exception — red/green there is the control,
// not decoration.
// "3 PM", or "9:20 AM" when there are minutes. For a time that did not come
// from the hour keys - see fmtSchedule.
function fmtTimeLabel(value) {
    // An HH:MM is the only thing this reads. Anything else - a blank hour above
    // all - gets nothing back, so fmtSchedule falls through to the bare date
    // rather than calling a missing time midnight.
    const parts = String(value || "").split(":");
    if (parts.length < 2) {
        return "";
    }
    const [hours, minutes] = parts.map(Number);
    if (!Number.isInteger(hours) || hours < 0 || hours > 23) {
        return "";
    }
    const meridiem = hours < 12 ? "AM" : "PM";
    const hour12 = hours % 12 || 12;
    return minutes
        ? `${hour12}:${String(minutes).padStart(2, "0")} ${meridiem}`
        : `${hour12} ${meridiem}`;
}

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

// Where a phone number is OPTIONAL. A number is how the shop gets back in touch,
// so it is required wherever the laundry leaves the counter or the customer is not
// the one standing there: Drop-off & Delivery, Pickup & Delivery, Locker. A plain
// Drop-off or Self-service customer hands the bag over and collects it in person,
// and demanding a number there is what filled the book with 00000000000 contacts -
// a dummy number is worse than a blank one, because a dummy number MATCHES (see
// the one-customer-per-number rule in models/res_partner.py).
const PHONE_OPTIONAL_SERVICE_TYPES = new Set(["dropoff", "self_service"]);

// Where a customer TAG is required. A tag is how this shop classifies a
// customer, and it is the one classification that travels with the order to
// the receipt (res.partner.pos_tag_names). Locker only, by the owner's rule:
// a locker bag is handed to a machine rather than to a cashier, so the tag is
// the only thing on the order that says what kind of customer left it. The
// other four put a person in front of someone who can see them.
const TAGS_REQUIRED_SERVICE_TYPES = new Set(["locker"]);

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
            // Flipped by a failed Continue, never on its own: the modal shouldn't
            // scold a cashier about a section they haven't reached yet.
            showErrors: false,
            // Step 1 — Customer
            customerType: null,
            partnerQuery: "",
            selectedPartner: null,
            // Step 2 — Services: pressing a pill adds the product to the REAL
            // POS order. `rev` is bumped on each press to recompute pill state.
            rev: 0,
            // Step 3 — Service Type
            serviceType: null,
            // Step 4 — Schedule (flat keys to keep OWL reactivity simple).
            // No date is pre-selected: the strip OPENS on today, but picking is the
            // cashier's act, so a schedule can't be confirmed without being looked at.
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

    // ── The phone number rule ─────────────────────────────────────────────

    get serviceTypeLabel() {
        const found = SERVICE_TYPES.find((t) => t.code === this.state.serviceType);
        return found ? found.label : "this service type";
    }

    get phoneRequired() {
        return (
            !!this.state.serviceType &&
            !PHONE_OPTIONAL_SERVICE_TYPES.has(this.state.serviceType)
        );
    }

    get tagsRequired() {
        return (
            !!this.state.serviceType &&
            TAGS_REQUIRED_SERVICE_TYPES.has(this.state.serviceType)
        );
    }

    // EVERY service type, with no exceptions. The address is what a bag is
    // delivered, collected and re-booked to, and it is the line that tells two
    // customers of the same name apart everywhere this module prints a contact
    // (_laundry_contact_block, the Orders list, the duplicate dialog). Both
    // lines, because this shop writes the kind of place on the first
    // ("RESIDENCE", "OFFICE") and the address itself on the second - one
    // without the other is half an address.
    get addressRequired() {
        return !!this.state.serviceType;
    }

    // Either field will do: the book carries numbers in both and either can be
    // called. Trimmed, so a contact holding a space is still treated as blank.
    partnerPhone(partner) {
        return String(partner?.phone || partner?.mobile || "").trim();
    }

    get selectedPartnerPhone() {
        return this.partnerPhone(this.state.selectedPartner);
    }

    partnerStreet(partner, key) {
        return String(partner?.[key] || "").trim();
    }

    /**
     * What a contact has to carry before this order can be taken, listed in the
     * order the contact form shows the fields.
     *
     * ONE table, so the three rules are read together: they are per service
     * type and they are not the same per type, which is impossible to see when
     * each is written as its own getter somewhere else in the file. Changing
     * what an order demands is changing `needed` here and nothing else.
     *
     * None of them is enforced on the contact FORM - a contact can always be
     * saved half-finished, and deliberately: a form that refuses is what makes
     * a cashier invent a value to get past it (the seven 00000000000 contacts).
     * The ORDER is what asks, because the order is what actually needs them.
     */
    get contactRequirements() {
        return [
            {
                label: "Phone number",
                needed: this.phoneRequired,
                has: (p) => !!this.partnerPhone(p),
            },
            {
                label: "Tag",
                needed: this.tagsRequired,
                has: (p) => this.partnerTags(p).length > 0,
            },
            {
                label: "Address line 1",
                needed: this.addressRequired,
                has: (p) => !!this.partnerStreet(p, "street"),
            },
            {
                label: "Address line 2",
                needed: this.addressRequired,
                has: (p) => !!this.partnerStreet(p, "street2"),
            },
        ];
    }

    // Named BEFORE any details have been taken, for a new customer who has no
    // contact yet: the cashier is told what the picker is going to ask for,
    // instead of finding out one refusal at a time.
    get requiredContactLabels() {
        return this.contactRequirements.filter((r) => r.needed).map((r) => r.label);
    }

    // And named again AFTER, as the gaps that are left on the contact chosen.
    get missingContactLabels() {
        const partner = this.state.selectedPartner;
        return this.contactRequirements
            .filter((r) => r.needed && !(partner && r.has(partner)))
            .map((r) => r.label);
    }

    // All of it can only come off a REAL contact - a number or an address typed
    // into this modal and left here would be held by no res.partner, so the
    // receipt, the order and the backend would each have a different idea of
    // it. So the gate asks for a selected partner that CARRIES the values, not
    // for strings.
    get contactOk() {
        // Nothing is asked before a service type is chosen: every rule above is
        // per-type, so until one is picked there is no question to answer.
        if (!this.state.serviceType) {
            return true;
        }
        return !!this.state.selectedPartner && !this.missingContactLabels.length;
    }

    get missingContactDetails() {
        return this.state.showErrors && !this.contactOk;
    }

    /**
     * Open the core customer picker ON TOP of this modal, so a number can be
     * added without losing the service type and schedule already keyed in.
     * Create and Edit both live in there, which is why one button serves a new
     * customer and a returning one whose record has no number.
     */
    async addCustomerDetails() {
        const order = this.pos.getOrder();
        if (!order) {
            return;
        }
        // Our own selectPartner override blocks the picker on an order with no
        // chosen purpose (the idle-order guard in overrides/pos_store.js). This
        // IS that purpose being filled in, so it is waved through for one call.
        order._laundryPartnerPickAllowed = true;
        try {
            await this.pos.selectPartner(order);
        } finally {
            order._laundryPartnerPickAllowed = false;
        }
        // Read it back off the ORDER rather than the return value: the picker
        // is what sets it there, and that is the value the rest of the module
        // treats as the customer.
        const partner = order.getPartner?.();
        if (partner) {
            this.state.selectedPartner = partner;
            this.state.partnerQuery = "";
        }
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
        const names = String(partner?.pos_tag_names || "");
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

    // Whether a leg of the schedule belongs to a booking made outside the
    // register, so its pickers give way to a plain reading of it. Locked PER LEG,
    // not for the pair: a booking that carried only one of them still fixes
    // that one, and leaves the cashier able to fill the other rather than
    // dead-ending an order nobody can confirm. Nothing here locks anything;
    // laundry_locker does, for a slot the locker customer already chose.
    get pickupLocked() {
        return false;
    }

    get pdDelLocked() {
        return false;
    }

    // Whether the read-only summary of slots chosen elsewhere is on screen at
    // all. Its own answer is just the two legs above, but it is a getter so a
    // module that adds a THIRD line to that box can extend it and have the box
    // appear for that line alone - laundry_locker does, for the deposit time,
    // which is there even on a booking that carried no pickup slot.
    get lockedSlots() {
        return this.pickupLocked || this.pdDelLocked;
    }

    // A picked date and hour as one line, for a schedule being shown rather
    // than chosen: "Sep 27, 3 PM".
    fmtSchedule(dateVal, hourVal) {
        const date = fmtDateLabel(dateVal);
        if (!date) {
            return "\u2014";
        }
        const hour = this.hours.find((h) => h.value === hourVal);
        if (hour) {
            return `${date}, ${hour.label}`;
        }
        // A time that is NOT one of the pickable hours, so it has no label to
        // borrow: a moment recorded elsewhere and carrying minutes, like the
        // locker deposit. Written out from the HH:MM rather than dropped, which
        // is what an unmatched hour used to be.
        const time = fmtTimeLabel(hourVal);
        return time ? `${date}, ${time}` : date;
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

    // ── Turnaround calculation ────────────────────────────────
    // Ported from the old pos.html POS, and no longer a mirror of it: the
    // returning-customer threshold was dropped here and not there. Nothing
    // keeps the two in step, so this is the rule - a turnaround quoted
    // anywhere else is checked against THIS, not against pos.html.

    // ONE threshold for everybody. A returning customer used to get a shorter
    // one - 18 hrs instead of 24, or 48 instead of 72 - so the same schedule
    // read Regular for them and Express for a walk-in. Two prices for one
    // promise, off a distinction nobody at the counter could state out loud.
    //
    // The first-time customer's numbers are the ones kept, so the band that
    // used to be a returning customer's Regular (18 up to 24 hrs, or 48 up to
    // 72) is now Express for them too. That is the point of dropping it.
    //
    // customerType still says who the order is FOR - a Returning one has to
    // have a contact picked - it just no longer decides what it costs.
    get turnaroundThreshold() {
        return this.hasLongService ? 72 : 24;
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

    // ── What's missing (only ever true after a failed Continue) ───────────

    get missingCustomerType() {
        return this.state.showErrors && !this.state.customerType;
    }

    // A Returning customer must have a partner actually selected; a New one doesn't.
    get missingPartner() {
        return (
            this.state.showErrors &&
            this.state.customerType === "returning" &&
            !this.state.selectedPartner
        );
    }

    get missingCustomer() {
        return this.missingCustomerType || this.missingPartner || this.missingContactDetails;
    }

    get missingServiceType() {
        return this.state.showErrors && !this.state.serviceType;
    }

    get missingServices() {
        return (
            this.state.showErrors &&
            !!this.state.serviceType &&
            this.state.serviceType !== "self_service" &&
            !this.hasAnyService
        );
    }

    // Takes the state key, so the date/hour sub-templates can ask about whichever
    // field they were handed rather than each caller spelling it out.
    isMissing(key) {
        return this.state.showErrors && !this.state[key];
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
            // The customer has to carry what this service type needs - a number
            // to call, an address to deliver to, a tag on a locker order. See
            // contactRequirements.
            this.contactOk &&
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
        // Continue stays enabled when the form is incomplete: a dead button says
        // nothing about WHY. Pressing it marks every unfilled section in red instead.
        if (!this.canConfirm) {
            this.state.showErrors = true;
            return;
        }
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
