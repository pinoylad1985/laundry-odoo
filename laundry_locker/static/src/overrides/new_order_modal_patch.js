/** @odoo-module **/

import { useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { NewOrderModal } from "@laundry_pos/new_order_modal/new_order_modal";
import { LockerPickerPopup } from "@laundry_locker/locker_picker/locker_picker_popup";

// What the customer paid for at the locker, in the feed's own words
// ("Wash, Dry & Fold", "Dry Clean, Press"), mapped onto the modal's own
// service codes. Keyed on the MOST specific wording first: "dry clean" has to
// win before "wash-dry-fold" is tried, or a dry clean reads as a fold.
const LOCKER_SERVICE_KEYWORDS = [
    ["shoe", ["shoe"]],
    ["cap", ["cap"]],
    ["press", ["press", "iron"]],
    ["dwc", ["dry/wet", "dry clean", "wet clean", "dry cleaning", "dwc"]],
    ["wdf", ["wash-dry-fold", "wash dry fold", "wash/dry/fold", "wdf", "wash"]],
];

function lockerServiceCodes(service) {
    const text = (service || "").toLowerCase();
    if (!text) {
        return [];
    }
    const codes = [];
    // Each listed service is read on its own, so a booking for two of them
    // pre-fills both.
    for (const part of text.split(/[,;+]/)) {
        const piece = part.trim();
        if (!piece) {
            continue;
        }
        for (const [code, keywords] of LOCKER_SERVICE_KEYWORDS) {
            if (keywords.some((keyword) => piece.includes(keyword))) {
                if (!codes.includes(code)) {
                    codes.push(code);
                }
                break;
            }
        }
    }
    return codes;
}

// Picking LOCKER as the service type opens the unbilled locker queue: the
// drop-off already happened, so the customer is looked up rather than typed.
patch(NewOrderModal.prototype, {
    setup() {
        super.setup(...arguments);
        this.lockerOrm = useService("orm");
        this.notification = useService("notification");
        this.lockerState = useState({ claim: null, requiredServices: [] });

        // A Locker order being edited (Change, or after a reload) carries its
        // ref on the order, so the block shows what was already taken instead
        // of sending the cashier back to the queue.
        const ref = this.pos.getOrder()?.laundry_locker_ref;
        if (ref) {
            this.lockerState.claim = {
                ref, partner_name: "", phone: "", dirty_door: "",
                id: null, phone_verified: false,
            };
            // The ref is the durable fact - it is written onto the order the
            // moment a drop-off is taken - so an order carrying one IS a Locker
            // order, whether or not Continue was ever pressed on it. Switching
            // the type away is the only thing that gives it back, and that
            // clears the ref, so there is no order with a ref and some other
            // type to overrule here.
            //
            // Without this, dismissing the modal after taking a drop-off
            // reopened it with NO service type selected while the customer and
            // the service lines were still on the order: the claim sat behind a
            // Locker key nobody had pressed.
            this.state.serviceType = "locker";
            this._loadLockerClaim(ref);
        }
    },

    // Read through the server's own _pos_row, so the claim an order being
    // edited puts back is the SAME shape the picker hands over - the phone
    // written the way the shop writes it, the matched customer's name, and the
    // deposit hour the turnaround is measured from. Assembling a claim from a
    // field list here instead is what let a reopened order be missing something
    // a fresh one had, and the schedule below is the one thing NOT taken from
    // it: the order's own stored slots have already been restored by then.
    async _loadLockerClaim(ref) {
        const row = await this.lockerOrm.call(
            "laundry.locker.transaction", "get_row_for_pos_ref", [ref]
        );
        if (!row) {
            return;
        }
        this.lockerState.claim = row;
        // Re-arms the guard on an order being edited. Only ADDS what is
        // missing, so reopening a set-up order changes nothing.
        this._applyLockerServices(row.service);
        // And the promised slots, which the modal has no other copy of unless
        // Continue was pressed: the schedule the cashier can see is state, and
        // state does not survive the modal being closed. Only ever fills a slot
        // the booking actually carried, so a leg the cashier was left to choose
        // is not overwritten with a blank.
        if (this.state.serviceType === "locker") {
            this._applyLockerSchedule(row.schedule);
        }
    },

    selectServiceType(code) {
        const previous = this.state.serviceType;
        super.selectServiceType(...arguments);
        if (code === "locker") {
            if (this.lockerState.claim) {
                // super clears the schedule on EVERY pick, a re-pick of the
                // same type included. A drop-off already taken keeps the slots
                // its customer was promised, so they go straight back - left
                // alone, pressing Locker on an order that already holds one
                // offered the cashier pickers for two slots they are not
                // allowed to choose.
                this._applyLockerSchedule(this.lockerState.claim.schedule);
                this._applyLockerServices(this.lockerState.claim.service);
            } else {
                // Deliberately not awaited - selectServiceType is called from
                // the template and the modal stays usable behind the picker.
                this.openLockerPicker(false).then((taken) => {
                    if (!taken) {
                        this._revertLockerServiceType(previous);
                    }
                });
            }
        } else if (previous === "locker") {
            this.releaseLockerClaim();
        }
    },

    // Dropping a drop-off is purely local: picking one never took it off
    // anyone, so there is nothing to give back. Unbilling here would be
    // actively wrong - it would free a drop-off that a DIFFERENT till may
    // have meanwhile sold.
    releaseLockerClaim() {
        this.lockerState.claim = null;
        this.lockerState.requiredServices = [];
        const order = this.pos.getOrder();
        if (order) {
            order.laundry_locker_ref = false;
            order.laundry_locker_door = false;
        }
    },

    // Closing the queue without taking a drop-off is not a way INTO a Locker
    // order. Everything that makes one is on the row - the ref, the dirty
    // door, the customer the locker screen was keyed with, and the slot that
    // customer was promised - and none of it can be typed in at the till from
    // memory. Left alone, dismissing the picker used to leave the order set to
    // Locker with all of that blank, and the cashier keyed it in by hand.
    //
    // So the tap that opened the picker is undone and the service type goes
    // back to what it was. The schedule stays cleared: selectServiceType wiped
    // it on the way in and there is no copy of it to put back.
    _revertLockerServiceType(previous) {
        // Only ever undoes ITS OWN tap: a claim taken meanwhile, or a service
        // type since changed, is the cashier's and stands.
        if (this.lockerState.claim || this.state.serviceType !== "locker") {
            return;
        }
        this.state.serviceType = previous;
        this.notification.add(
            "A Locker order has to be taken from the drop-off queue.",
            { type: "warning" }
        );
    },

    // A Locker order IS a drop-off, so Continue does not accept one without.
    // The picker is now the only way into a fresh Locker order, but an order
    // reopened from history can still arrive here as Locker with its ref lost,
    // and there the rule still has to hold - so it is stated where it is
    // enforced rather than left to the door orders usually come through.
    get canConfirm() {
        if (this.state.serviceType === "locker" && !this.lockerState.claim) {
            return false;
        }
        return super.canConfirm;
    },

    get missingLockerClaim() {
        return (
            this.state.showErrors &&
            this.state.serviceType === "locker" &&
            !this.lockerState.claim
        );
    },

    /**
     * @param {boolean} recheck - reopen on the transaction already taken (to
     *   correct the number) instead of showing the queue.
     * @returns {Promise<boolean>} whether a drop-off was taken.
     */
    async openLockerPicker(recheck = false) {
        const previous = this.lockerState.claim;
        const result = await makeAwaitable(this.dialog, LockerPickerPopup, {
            transactionId: recheck && previous?.id ? previous.id : undefined,
        });
        if (!result) {
            return false;
        }
        // Swapping to another drop-off needs no release either - see
        // releaseLockerClaim.
        this.lockerState.claim = result;

        const order = this.pos.getOrder();
        if (order) {
            order.laundry_locker_ref = result.ref;
            // Copied ONTO the order so the receipt can print it: a reprint
            // from order history runs on tills that never loaded the queue.
            order.laundry_locker_door = result.dirty_door || false;
        }
        // Always "returning" in the modal's terms: a claim always ends with a
        // real contact (created there and then for a new number), so the
        // customer step has a partner to show. The locker's own
        // Returning/New is shown in the picker, and means something else.
        await this._selectLockerPartner(result.partner_id);
        if (this.state.selectedPartner) {
            this.state.customerType = "returning";
        }
        this._applyLockerSchedule(result.schedule);
        this._applyLockerServices(result.service);
        return true;
    },

    // The booking is the till's instruction, not a suggestion: the services it
    // was made for go on the order at one each, and the last one of each
    // cannot be taken back off. A cashier who needs more presses adds them;
    // one who thinks the customer did not book a fold is reading the booking
    // wrong, and the fix for that is a different drop-off, not a smaller sale.
    _applyLockerServices(service) {
        const codes = lockerServiceCodes(service);
        this.lockerState.requiredServices = codes;
        for (const code of codes) {
            if (!this.serviceCount(code)) {
                this.addService(code);
            }
        }
    },

    removeService(code) {
        if (
            this.lockerState.requiredServices.includes(code) &&
            this.serviceCount(code) <= 1
        ) {
            this.notification.add(
                "This service came with the locker booking and cannot be removed.",
                { type: "warning" }
            );
            return;
        }
        super.removeService(...arguments);
    },

    // The slot the customer picked at the locker is the one they were promised,
    // so the till READS it and cannot re-pick it. Each leg is judged on its
    // own: a booking that carried only a pickup fixes the pickup and still
    // lets the cashier choose the delivery, rather than dead-ending an order
    // that could never be confirmed.
    get pickupLocked() {
        return this._lockerSlotLocked(this.state.pickupDate);
    },

    get pdDelLocked() {
        return this._lockerSlotLocked(this.state.pdDelDate);
    },

    _lockerSlotLocked(value) {
        return (
            this.state.serviceType === "locker" &&
            !!this.lockerState.claim &&
            !!value
        );
    },

    // The moment the bag became laundry, as the summary box reads it - to the
    // minute, because that is what the turnaround is measured from and the line
    // on screen has to be the same figure. Blank off a Locker order, and blank
    // until the claim has been read back, so the box does not flash an empty
    // line while that is in flight.
    get lockerDeposit() {
        if (this.state.serviceType !== "locker") {
            return "";
        }
        const deposit = this.lockerState.claim?.schedule?.deposit;
        return deposit?.date ? this.fmtSchedule(deposit.date, deposit.hour) : "";
    },

    // A drop-off with no pickup slot still has a deposit time to show, so the
    // box has to come up for that line on its own.
    get lockedSlots() {
        return super.lockedSlots || !!this.lockerDeposit;
    },

    // A Locker order's turnaround runs from the DEPOSIT, not from the pickup.
    // The customer has been waiting since the bag went into the door - the van
    // collecting it hours later is the shop's own scheduling and is not the
    // customer's wait - so measuring from the pickup quoted a shorter
    // turnaround than the one actually being delivered, and charged express for
    // it.
    //
    // The deposit is used to the MINUTE, not rounded to an hour: rounding moves
    // the figure by up to an hour, which is enough to cross a 24-hour threshold
    // on its own and bill the order the other way. Only this end carries
    // minutes - the delivery is an hour key - so the hours here are fractional
    // and Math.round settles the label, the same rounding every other service
    // type already gets from super.
    //
    // Falls back to the pickup->delivery reading only when there is no deposit
    // time at all, which would be a drop-off the feed never dated.
    get diffHours() {
        if (this.state.serviceType !== "locker") {
            return super.diffHours;
        }
        const deposit = this.lockerState.claim?.schedule?.deposit;
        const from = this._ms(deposit?.date, deposit?.hour);
        if (!from) {
            return super.diffHours;
        }
        const back = this._ms(this.state.pdDelDate, this.state.pdDelHour);
        return back ? Math.round((back - from) / 3_600_000) : null;
    },

    _applyLockerSchedule(schedule) {
        if (!schedule) {
            return;
        }
        // Locker shares pickup_delivery's state keys: the RETURN leg is
        // pdDel*, not delivery*, which belongs to drop-off & delivery.
        if (schedule.pickup?.date) {
            this.state.pickupDate = schedule.pickup.date;
            this.state.pickupHour = schedule.pickup.hour;
        }
        if (schedule.delivery?.date) {
            this.state.pdDelDate = schedule.delivery.date;
            this.state.pdDelHour = schedule.delivery.hour;
        }
    },

    // Fetched from the server EVERY time, the same way the modal's own search
    // does. Not just because the matched customer may not be one of the
    // partners the session pre-loaded: claiming may have just written the
    // locker address onto a customer the session loaded long ago, and a stale
    // copy would print a blank address on this order's receipt.
    async _selectLockerPartner(partnerId) {
        if (!partnerId) {
            return;
        }
        await this.pos.data.callRelated("res.partner", "get_new_partner", [
            this.pos.config.id,
            [["id", "=", partnerId]],
            0,
        ]);
        const partner = (this.pos.models["res.partner"]?.getAll() ?? []).find(
            (p) => p.id === partnerId
        );
        if (partner) {
            this.pickPartner(partner);
        }
    },
});
