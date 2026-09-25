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
            this.lockerState.claim = { ref, partner_name: "", phone: "", id: null };
            this._loadLockerClaim(ref);
        }
    },

    async _loadLockerClaim(ref) {
        const rows = await this.lockerOrm.searchRead(
            "laundry.locker.transaction",
            [["ref", "=", ref]],
            ["id", "ref", "phone", "customer_name", "partner_id"],
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
        };
    },

    selectServiceType(code) {
        const previous = this.state.serviceType;
        super.selectServiceType(...arguments);
        if (code === "locker") {
            if (!this.lockerState.claim) {
                this.openLockerPicker(false);
            }
        } else if (previous === "locker") {
            // Switching away releases the drop-off, or it would sit billed
            // against an order that no longer bills it.
            this.releaseLockerClaim();
        }
    },

    async releaseLockerClaim() {
        const claim = this.lockerState.claim;
        this.lockerState.claim = null;
        const order = this.pos.getOrder();
        if (order) {
            order.laundry_locker_ref = false;
        }
        if (claim?.id) {
            await this.lockerOrm.call(
                "laundry.locker.transaction", "action_unbill", [claim.id]
            );
        }
    },

    /**
     * @param {boolean} recheck - reopen on the transaction already taken (to
     *   correct the number) instead of showing the queue.
     */
    async openLockerPicker(recheck = false) {
        const previous = this.lockerState.claim;
        const result = await makeAwaitable(this.dialog, LockerPickerPopup, {
            transactionId: recheck && previous?.id ? previous.id : undefined,
        });
        if (!result) {
            return;
        }
        // Swapping to a different drop-off releases the first one, or it would
        // sit billed against an order that never bills it.
        if (previous?.id && previous.id !== result.id) {
            await this.lockerOrm.call(
                "laundry.locker.transaction", "action_unbill", [previous.id]
            );
        }
        this.lockerState.claim = result;

        const order = this.pos.getOrder();
        if (order) {
            order.laundry_locker_ref = result.ref;
        }
        // Always "returning" in the modal's terms: a claim always ends with a
        // real contact (created there and then for a new number), so the
        // customer step has a partner to show. The locker's own
        // Returning/New is shown in the picker, and means something else.
        await this._selectLockerPartner(result.partner_id);
        if (this.state.selectedPartner) {
            this.state.customerType = "returning";
        }
    },

    // The matched customer may not be one of the partners the session
    // pre-loaded, so fetch it the same way the modal's own server search does.
    async _selectLockerPartner(partnerId) {
        if (!partnerId) {
            return;
        }
        const find = () =>
            (this.pos.models["res.partner"]?.getAll() ?? []).find((p) => p.id === partnerId);
        let partner = find();
        if (!partner) {
            await this.pos.data.callRelated("res.partner", "get_new_partner", [
                this.pos.config.id,
                [["id", "=", partnerId]],
                0,
            ]);
            partner = find();
        }
        if (partner) {
            this.pickPartner(partner);
        }
    },
});
