/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

// The unbilled locker queue, shown when LOCKER is picked in the New Order
// modal. Picking a row hands its customer back to the modal and RESERVES
// NOTHING - the drop-off is billed against whichever order is validated first
// (see pos.order._sync_laundry_locker).
//
// The customer is SHOWN here, not typed. Locker customers key their own number
// into the PudoPro screen and do get it wrong, so a correction has to be
// possible - but only where it is warranted:
//   - a number that MATCHED a contact on its last 10 digits is settled; the
//     matched contact is displayed and nothing is editable, or the register would
//     be free to put a second spelling of a known customer onto the order.
//   - a number that matched nothing is CALLED first, and the cashier answers
//     one question: did it ring? "It rang" is what lets a contact be created
//     from it; "it did NOT ring" is the ONLY thing that unlocks the name and
//     phone for a correction. A number nobody has dialed is neither creatable
//     nor editable.
export class LockerPickerPopup extends Component {
    static template = "laundry_locker.LockerPickerPopup";
    static components = { Dialog };
    static props = {
        close: Function,
        getPayload: Function,
        // Set to re-open on a transaction this register already took, to correct
        // the number. The list step is skipped and the billed guard is waived.
        transactionId: { type: Number, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            rows: [],
            query: "",
            selectedId: null,
            // The editable copy of the selected row's customer details.
            phone: "",
            customerName: "",
            match: null,          // 'returning' | 'new' | null
            partnerName: "",
            // The matched customer as name / phone / address, built
            // server-side by laundry_pos so this reads the same as the
            // customer block in the Orders list.
            partnerLines: [],
            // The call's two outcomes, and the only two ways out of the gate:
            // verified = it rang, editing = it did not and the number is being
            // corrected. Both false = nobody has called it yet.
            verified: false,
            editing: false,
            checking: false,
            busy: false,
            error: "",
        });
        onWillStart(() => this.loadRows());
    }

    get isRecheck() {
        return !!this.props.transactionId;
    }

    async loadRows() {
        if (this.isRecheck) {
            this.state.rows = await this.orm.call(
                "laundry.locker.transaction", "get_rows_for_pos", [[this.props.transactionId]]
            );
            if (this.state.rows.length) {
                this.selectRow(this.state.rows[0]);
            }
            return;
        }
        this.state.rows = await this.orm.call(
            "laundry.locker.transaction", "get_unbilled_for_pos", []
        );
    }

    get filteredRows() {
        const query = this.state.query.trim().toLowerCase();
        if (!query) {
            return this.state.rows;
        }
        // Every word has to match somewhere in the row, so "rise 0917" works.
        const words = query.split(/\s+/);
        return this.state.rows.filter((row) => {
            const haystack = [
                row.ref, row.customer_name, row.phone, row.location_name,
                row.service, row.turnaround, row.dirty_door, row.status_label,
                row.new_laundry_at, row.pickup_datetime, row.delivery_datetime,
            ].join(" ").toLowerCase();
            return words.every((word) => haystack.includes(word));
        });
    }

    get selectedRow() {
        return this.state.rows.find((row) => row.id === this.state.selectedId) || null;
    }

    selectRow(row) {
        this.state.selectedId = row.id;
        this.state.phone = row.phone || "";
        this.state.customerName = row.customer_name || "";
        this.state.match = row.customer_match || "new";
        this.state.partnerName = row.partner_name || "";
        this.state.partnerLines = row.partner_lines || [];
        this.state.verified = !!row.phone_verified;
        // A recheck opens straight into the correction: the cashier got here
        // by pressing "Correct number", which says the same thing as answering
        // that it did not ring. Claiming the row already created its contact, so
        // it would otherwise read as a settled match and lock them out of the
        // one screen they opened to fix.
        this.state.editing = this.isRecheck;
        this.state.error = "";
    }

    back() {
        if (this.isRecheck) {
            this.props.close();
            return;
        }
        this.state.selectedId = null;
        this.state.error = "";
    }

    onPhoneInput(ev) {
        this.state.phone = ev.target.value;
        // The match shown belongs to the number it was looked up for, so a
        // half-typed correction must not keep claiming "Returning".
        this.state.match = null;
        this.state.partnerName = "";
        this.state.partnerLines = [];
    }

    // Re-run the Returning/New lookup for the corrected number.
    async checkPhone() {
        if (this.state.checking) {
            return;
        }
        this.state.checking = true;
        try {
            const result = await this.orm.call(
                "laundry.locker.transaction", "pos_check_phone", [this.state.phone]
            );
            this.state.match = result.customer_match;
            this.state.partnerName = result.partner_name || "";
            this.state.partnerLines = result.partner_lines || [];
        } finally {
            this.state.checking = false;
        }
        // The correction has been answered, so the fields close again: a match
        // is settled, and a number that still matches nothing has to be called
        // in its own right before it can become a contact.
        this.state.editing = false;
        this.state.verified = false;
    }

    // "I called it and it rang" - the one thing that lets a contact be created
    // from a number no customer has.
    onPhoneRang() {
        this.state.verified = true;
        this.state.editing = false;
        this.state.error = "";
    }

    // "I called it and it did NOT ring" - the one thing that unlocks the name
    // and phone.
    onPhoneWrong() {
        this.state.verified = false;
        this.state.editing = true;
        this.state.error = "";
    }

    // A matched contact is the answer to the lookup, not a suggestion: it is
    // displayed, and the fields stay shut.
    get isReturning() {
        return this.state.match === "returning";
    }

    get canEditCustomer() {
        return this.state.editing;
    }

    // The matched customer's three lines. The fallback is for a row read
    // before this register's assets were updated, where the name is all the
    // server sent.
    get returningLines() {
        if (this.state.partnerLines.length) {
            return this.state.partnerLines;
        }
        return [this.state.partnerName, this.state.phone].filter((l) => l);
    }

    // The contact Confirm is about to CREATE, in the same three lines a
    // matched customer is shown in. The address is not a guess: a contact
    // created from a locker drop-off is given the locker as its address when
    // it has none (_apply_locker_address), and a brand-new contact never has
    // one - so this is what will be in the book, shown before it is written
    // rather than found afterwards.
    get newContactLines() {
        const site = this.selectedRow ? this.selectedRow.location_name : "";
        return [
            this.state.customerName,
            this.state.phone,
            site ? site + " LOCKER" : "",
        ].filter((line) => line);
    }

    // A new customer is only created off a number someone has called.
    get needsVerification() {
        return this.state.match === "new" && !this.state.verified;
    }

    get confirmDisabled() {
        return this.state.busy || !this.state.selectedId || !this.state.match;
    }

    async confirm() {
        if (this.confirmDisabled) {
            return;
        }
        // Reachable only from the correction with nothing retyped: the
        // buttons that message points at are not on screen while the fields
        // are, so the correction gets its own prompt.
        if (this.state.editing) {
            this.state.error = "Press Check to look up the number you put in.";
            return;
        }
        if (this.needsVerification) {
            this.state.error =
                "Call \u201c" + this.state.phone + "\u201d first, then press " +
                "\u201cI called it and it rang\u201d \u2014 or \u201cI called it and " +
                "it did NOT ring\u201d if nobody can be reached on it.";
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.orm.call(
                "laundry.locker.transaction", "pos_claim", [this.state.selectedId], {
                    phone: this.state.phone,
                    customer_name: this.state.customerName,
                    phone_verified: this.state.verified,
                }
            );
            this.props.getPayload(result);
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            throw error;
        }
    }

    matchLabel(row) {
        return row.customer_match === "returning" ? "Returning" : "New";
    }

    matchClass(row) {
        return row.customer_match === "returning" ? "text-bg-success" : "text-bg-warning";
    }
}
