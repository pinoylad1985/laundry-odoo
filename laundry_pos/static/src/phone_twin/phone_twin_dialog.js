/** @odoo-module **/

import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

// "This phone number is already used by ..." - asked as a CHOICE, not refused.
//
// The server rule (res.partner.create / write, laundry_pos) still refuses a
// duplicate outright, and has to: it is what covers imports, the locker and
// anything else that writes a contact. But a refusal is the wrong thing to
// show a cashier, because being told no is not the answer they need. The
// number is already in the book, so there IS a customer to serve - they just
// have to be told who, and offered them.
//
// It names the customer in the same three lines the POS Orders list uses
// (name, phone, address, from res.partner._laundry_contact_block), because
// "already used by MARY" is not something anyone can act on: the book holds
// several people called MARY, and the address is what tells them apart.
export class PhoneTwinDialog extends Component {
    static template = "laundry_pos.PhoneTwinDialog";
    static components = { Dialog };
    static props = {
        // The twin, as name / phone / address lines.
        lines: Array,
        archived: { type: Boolean, optional: true },
        // What Confirm and Cancel will DO, in the words of the screen that
        // opened this. The two screens mean different things by "use that
        // customer" - the register puts them on the order, the contact form
        // opens their record - and a dialog that said only "Confirm" would be
        // asking the cashier to guess which.
        confirmHint: String,
        cancelHint: String,
        // An archived twin cannot simply be used, so the choice is not
        // offered - see the note in the template.
        allowConfirm: { type: Boolean, optional: true },
        onChoice: Function,
        close: Function,
    };

    get canConfirm() {
        return this.props.allowConfirm !== false && !this.props.archived;
    }

    choose(choice) {
        this.props.onChoice(choice);
        this.props.close();
    }
}

// Ask who holds this number, and let the caller's screen act on the answer.
//
// Returns {status: 'free'} | {status: 'placeholder'} | {status, twin} where
// status is 'confirm' (use that customer) or 'cancel' (give a different
// number). Closing the dialog counts as Cancel: the gentler of the two.
export async function laundryPhoneTwinChoice({
    orm, dialog, phone, excludeId, confirmHint, cancelHint, allowConfirm,
}) {
    const info = await orm.call(
        "res.partner", "laundry_phone_twin_info", [phone, excludeId || false]
    );
    if (!info) {
        return { status: "free" };
    }
    // A filler is refused, not offered: there is no customer behind
    // `00000000000` to put on an order, only a number to fix. Answering it
    // with the stranger who happens to hold those zeros would be true and
    // useless.
    if (info.placeholder) {
        await new Promise((resolve) => {
            dialog.add(AlertDialog, {
                title: "That is not a phone number",
                body:
                    phone + " is not a phone number. Enter the number the " +
                    "shop would actually call, or leave it blank - a number " +
                    "is only required on Drop-off & Delivery, Pickup & " +
                    "Delivery and Locker orders, where the laundry leaves " +
                    "the counter.",
            }, { onClose: resolve });
        });
        return { status: "placeholder" };
    }
    const choice = await new Promise((resolve) => {
        let answered = "cancel";
        const props = {
            lines: info.lines || [info.name],
            archived: !!info.archived,
            confirmHint,
            cancelHint,
            onChoice: (value) => {
                answered = value;
            },
        };
        // Only when the caller actually said so: OWL type-checks a prop that
        // is PRESENT, so passing `undefined` for an optional Boolean is not
        // the same as leaving it out.
        if (typeof allowConfirm === "boolean") {
            props.allowConfirm = allowConfirm;
        }
        dialog.add(PhoneTwinDialog, props, { onClose: () => resolve(answered) });
    });
    return { status: choice, twin: info };
}
