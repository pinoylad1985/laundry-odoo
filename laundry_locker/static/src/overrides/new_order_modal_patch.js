/** @odoo-module **/

import { useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { NewOrderModal } from "@laundry_pos/new_order_modal/new_order_modal";
import { LockerPickerPopup } from "@laundry_locker/locker_picker/locker_picker_popup";

// Picking LOCKER as the service type opens the unbilled locker queue: the
// drop-off already happened, so the customer is looked up rather than typed.
patch(NewOrderModal.prototype, {
    setup() {
        super.setup(...arguments);
        this.lockerOrm = useService("orm");
        this.lockerState = useState({ claim: null });

        // A Locker order being edited (Change, or after a reload) carries its
        // ref on the order, so the block shows what was already taken instead
        // of sending the cashier back to the queue.
        const ref = this.pos.getOrder()?.laundry_locker_ref;
        if (ref) {
            this.lockerState.claim = {
                ref, partner_name: "", phone: "", dirty_door: "", id: null,
            };
            this._loadLockerClaim(ref);
        }
    },

    async _loadLockerClaim(ref) {
        const rows = await this.lockerOrm.searchRead(
            "laundry.locker.transaction",
            [["ref", "=", ref]],
            ["id", "ref", "phone", "customer_name", "partner_id", "dirty_door"],
            { limit: 1 }
        );
        const row = rows[0];
        if (!row) {
            return;
        }
        this.lockerState.claim = {
            id: row.id,
            ref: row.ref,
            phone: row.phone || "",
            customer_name: row.customer_name || "",
            partner_name: row.partner_id ? row.partner_id[1] : "",
            dirty_door: row.dirty_door || "",
        };
    },

    selectServiceType(code) {
        const previous = this.state.serviceType;
        super.selectServiceType(...arguments);
        if (code === "locker") {
            if (!this.lockerState.claim) {
                this.openLockerPicker();
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
        const order = this.pos.getOrder();
        if (order) {
            order.laundry_locker_ref = false;
            order.laundry_locker_door = false;
        }
    },

    async openLockerPicker() {
        const result = await makeAwaitable(this.dialog, LockerPickerPopup, {});
        if (!result) {
            return;
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
    },

    // The slot the customer picked at the locker is the one they were promised,
    // so the till reads it rather than re-picking it. Locked only when the
    // booking actually carried both slots - a drop-off made without them would
    // otherwise leave the cashier with two empty fields they cannot fill.
    get scheduleLocked() {
        return (
            this.state.serviceType === "locker" &&
            !!this.lockerState.claim &&
            !!this.state.pickupDate &&
            !!this.state.pdDelDate
        );
    },

    get scheduleLockedNote() {
        if (!this.scheduleLocked) {
            return super.scheduleLockedNote;
        }
        return "Set by the locker booking - this is the slot the customer was promised.";
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
