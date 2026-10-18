/** @odoo-module **/

import { registry } from "@web/core/registry";
import { formView } from "@web/views/form/form_view";
import { FormController } from "@web/views/form/form_controller";
import { useService } from "@web/core/utils/hooks";
import { laundryPhoneTwinChoice } from "@laundry_pos/phone_twin/phone_twin_dialog";

// The comparison key for a phone number: its last 10 digits - the same key
// phone_last10() uses on the server, and the only way numbers are compared
// anywhere in this module. `0917 123 4567` and `+639171234567` are one number
// and have to read as one here too.
const laundryPhoneKey = (value) => {
    const digits = (value || "").replace(/[^0-9]/g, "");
    return digits.length >= 10 ? digits.slice(-10) : "";
};

// The contact form, asking about a duplicate number instead of being refused
// one.
//
// ONE controller for both places a cashier types a number, because the
// register's "edit customer" IS this form: PosStore.editPartner opens the
// res.partner form through an action inside the POS, so a js_class on that
// view is in force at the counter as well as in the back office. Written once
// so the two cannot answer the same question differently; what differs is only
// what Confirm then DOES, which is branched on whether a POS session is
// running.
//
// Hooked on onWillSaveRecord (core: "if it returns false, it will prevent the
// save") rather than on the phone field. The question is only worth asking
// about a number somebody has finished typing, and a field-level check would
// put it to them mid-keystroke, between a 0 and a 9.
export class LaundryPartnerFormController extends FormController {
    setup() {
        super.setup();
        this.laundryOrm = useService("orm");
        this.laundryDialog = useService("dialog");
        this.laundryAction = useService("action");
        // Read off env rather than useService("pos"), which THROWS in the back
        // office, where that service does not exist.
        this.laundryPos = this.env.services.pos || null;
    }

    async onWillSaveRecord(record) {
        if ((await super.onWillSaveRecord(...arguments)) === false) {
            return false;
        }
        const phone = record.data.phone;
        if (!phone) {
            return;
        }
        // Child contacts are exempt here for the same reason they are exempt
        // on the server: a delivery address under a company legitimately
        // carries its parent's number.
        if (record.data.parent_id) {
            return;
        }
        // A save that does not CHANGE the number is waved through, exactly as
        // the server rule waves it through - the form re-sends `phone`
        // whatever was actually edited, and without this the seven contacts
        // already holding `00000000000` could never be edited again, not even
        // to give them a real number. Read back from the database rather than
        // guessed from the form's dirty state, because that is the value the
        // rule is really being asked about.
        if (record.resId) {
            const stored = await this.laundryOrm.read(
                "res.partner", [record.resId], ["phone"]
            );
            const typedKey = laundryPhoneKey(phone);
            // Both sides must HAVE a key - see the server rule. Two numbers
            // too short for one are not thereby the same number, and calling
            // them the same would skip the checks that refuse them.
            //
            // The character-for-character test is the escape valve for a
            // number the rules refuse and that is ALREADY in the book (`N/A`,
            // say): it has no key, so without this the form would refuse every
            // save of that contact, including the one fixing its name. Same
            // reasoning, and the same pair of tests, as res.partner.write.
            if (
                stored.length &&
                ((typedKey && laundryPhoneKey(stored[0].phone) === typedKey) ||
                    phone === (stored[0].phone || ""))
            ) {
                return;
            }
        }
        const inPos = !!this.laundryPos;
        const answer = await laundryPhoneTwinChoice({
            orm: this.laundryOrm,
            dialog: this.laundryDialog,
            phone,
            excludeId: record.resId || false,
            confirmHint: inPos
                ? "put that customer on this order. Nothing is saved here."
                : "open that customer. What you typed here is discarded.",
            restoreHint: inPos
                ? "put that customer back in the book and on this order. Nothing is saved here."
                : "put that contact back in the book and open it. What you typed here is discarded.",
            cancelHint: inPos
                ? "come back here and give this customer a different number."
                : "stay here and give this contact a different number.",
        });
        if (answer.status === "free") {
            return;
        }
        if (answer.status !== "confirm") {
            // Cancel, or a number the rules refuse outright: the save is
            // stopped and the form is left exactly as typed, so only the
            // number has to be retyped.
            return false;
        }
        // The save is stopped FIRST and the twin opened after, never the other
        // way round. This hook runs INSIDE record.save(), which core wraps in
        // the model's mutex (`save()` = `mutex.exec(() => this._save())`, and
        // `_save` is what calls us) - and `discard()` queues on that same
        // mutex. Awaiting it from here waits for a lock this very call holds:
        // the hook never returns, the save hangs with the form still dirty,
        // and Confirm does nothing at all. Deferring runs the work once
        // save() has returned and the mutex is free again.
        const twinId = answer.twin.id;
        // Restored FIRST, before anything tries to use it: an archived
        // contact is invisible to the customer list, to get_new_partner and
        // to an ordinary read, so using it in either place would quietly find
        // nothing.
        const restore = !!answer.twin.archived;
        setTimeout(async () => {
            if (restore) {
                await this.laundryOrm.call(
                    "res.partner", "laundry_restore_phone_twin", [twinId]
                );
            }
            if (inPos) {
                await this._laundryUseTwinInPos(twinId);
            } else {
                await this._laundryOpenTwin(twinId);
            }
        });
        return false;
    }

    // At the register, "use that customer" means put them on the order being
    // taken - the same thing picking them out of the customer list would have
    // done. Fetched from the server first, the way the locker picker fetches a
    // matched customer: the twin may not be one of the customers this session
    // pre-loaded.
    async _laundryUseTwinInPos(twinId) {
        const pos = this.laundryPos;
        // Left for PosStore.editPartner (overrides/pos_store.js) to pick up.
        // The customer list is what puts a customer on the order, and it uses
        // whatever editPartner hands back - which, after a save that was
        // stopped, is either nothing or the contact that was being edited.
        // Going through the flag means the choice travels core's own path
        // instead of racing it.
        pos._laundryPhoneTwinPick = twinId;
        // Set here as well, for any way into this form that does not come
        // through the customer list. The two agree by construction - it is one
        // id - so neither can contradict the other.
        await pos.data.callRelated("res.partner", "get_new_partner", [
            pos.config.id,
            [["id", "=", twinId]],
            0,
        ]);
        const partner = (pos.models["res.partner"]?.getAll() ?? []).find(
            (p) => p.id === twinId
        );
        if (partner) {
            pos.setPartnerToCurrentOrder(partner);
        }
        await this._laundryDiscard();
        // The register opens this form with target="new", so it is a dialog:
        // the same close core uses after saving a product from the register.
        this.laundryAction.doAction({ type: "ir.actions.act_window_close" });
    }

    // In the back office there is no order to put anybody on, so "use that
    // customer" means open their record. Discarded first, deliberately and
    // without a second prompt: the cashier has just been shown who holds the
    // number and answered that it is them, so the half-typed duplicate is the
    // thing they no longer want.
    async _laundryOpenTwin(twinId) {
        await this._laundryDiscard();
        this.laundryAction.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: twinId,
            views: [[false, "form"]],
            view_mode: "form",
        });
    }

    async _laundryDiscard() {
        try {
            await this.model.root.discard();
        } catch {
            // Nothing to discard, or a core that no longer spells it this way.
            // Leaving the record dirty only costs the standard "unsaved
            // changes" prompt on the way out, which is not worth failing over.
        }
    }
}

registry.category("views").add("laundry_partner_form", {
    ...formView,
    Controller: LaundryPartnerFormController,
});
