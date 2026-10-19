/** @odoo-module **/

import { Component, onWillStart, useRef, useState } from "@odoo/owl";
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
            // The tags the shop keeps, and the ones picked for a contact
            // about to be created. res.partner.category is NOT loaded into
            // the POS (pos_tag_names exists precisely because the relation
            // is not), so they are fetched.
            tags: [],
            tagIds: [],
            // What is typed into the tags box, and whether its list is down.
            tagQuery: "",
            tagOpen: false,
            // Set by a Confirm refused for want of a tag, and by nothing
            // else: it is what turns the box red, so it has to mean "the
            // cashier has just been refused", not "the box is empty". An
            // empty box is marked blue from the start.
            tagRefused: false,
            // The call's two outcomes, and the only two ways out of the gate:
            // verified = it rang, editing = it did not and the number is being
            // corrected. Both false = nobody has called it yet.
            verified: false,
            editing: false,
            checking: false,
            busy: false,
            error: "",
        });
        this.tagInput = useRef("tagInput");
        onWillStart(() => Promise.all([this.loadRows(), this.loadTags()]));
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

    // The tag list, read once when the picker opens. A failure is swallowed:
    // the queue is still a queue of bags to bill, and a cashier with no tags
    // to pick from is better off than one looking at a crashed dialog. What
    // it costs is the tag, which is then asked for on the contact form.
    async loadTags() {
        try {
            this.state.tags = await this.orm.call(
                "laundry.locker.transaction", "pos_customer_tags", []
            );
        } catch {
            this.state.tags = [];
        }
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
                // Every spelling of the name and the number: a customer
                // ringing up about a corrected drop-off quotes the ones THEY
                // typed, and the cashier searching for them would otherwise
                // find nothing.
                row.ref, row.customer_name, row.customer_name_source,
                row.customer_name_corrected, row.phone, row.phone_source,
                row.phone_corrected, row.location_name,
                row.service, row.turnaround, row.dirty_door, row.status_label,
                row.new_laundry_at, row.pickup_datetime, row.delivery_datetime,
            ].join(" ").toLowerCase();
            return words.every((word) => haystack.includes(word));
        });
    }

    // The corrected name as its own column, beside the locker's own.
    //
    // Whitespace is collapsed, CASE is not - the server's _pos_corrected_name
    // draws the same line, and for the same reason: two spellings of one
    // number are the same number, two spellings of one name are not the same
    // text, so retyping `Mary Smith` as `MARY SMITH` is a correction and the
    // column says so.
    get correctedCustomer() {
        const row = this.selectedRow;
        if (!row) {
            return "";
        }
        const current = (this.state.customerName || "").trim();
        if (!current) {
            return "";
        }
        const flat = (value) => (value || "").trim().split(/\s+/).join(" ");
        if (flat(current) === flat(row.customer_name_source)) {
            return "";
        }
        return current;
    }

    // The correction as its own column, beside the locker's own number.
    //
    // Read off the STATE rather than the row, so the column fills in while
    // the number is being typed - the cashier is correcting it and looking at
    // this table, and a column that only caught up after Confirm would be
    // telling them something they can no longer act on.
    //
    // Compared on DIGITS, like the server's _pos_corrected_phone: re-spacing
    // a number is not correcting it, and the two must not disagree about
    // whether this drop-off was corrected at all.
    get correctedPhone() {
        const row = this.selectedRow;
        if (!row) {
            return "";
        }
        const current = (this.state.phone || "").trim();
        if (!current) {
            return "";
        }
        const digits = (value) => (value || "").replace(/\D/g, "");
        if (digits(current) === digits(row.phone_source)) {
            return "";
        }
        return current;
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
        // Picked per drop-off, so backing out of one row and into another
        // cannot tag the second customer with the first one's answer.
        this.state.tagIds = [];
        this.state.tagQuery = "";
        this.state.tagOpen = false;
        this.state.tagRefused = false;
        // A recheck opens straight into the correction: the cashier got here
        // by pressing "Correct number", which says the same thing as answering
        // that it did not ring. Claiming the row already created its contact, so
        // it would otherwise read as a settled match and lock them out of the
        // one screen they opened to fix.
        //
        // So does a row whose number the shop's rules refuse. The number came
        // off the feed exactly as somebody keyed it at the locker, and there
        // is no point asking anyone to call a number that is missing digits:
        // the only way forward is to correct it, so the fields open on it and
        // say why.
        this.state.error = row.phone_problem || "";
        this.state.editing = this.isRecheck || !!row.phone_problem;
        if (row.phone_problem) {
            // The row claims "New", which is true of the number and useless:
            // nothing can be created from it. Cleared so Confirm stays
            // disabled until Check has been pressed on a number that works.
            this.state.match = null;
        }
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
        // And a refusal belongs to the number that earned it. Cleared as soon
        // as the cashier starts fixing it, so the complaint goes away with
        // the thing complained about instead of hanging over the next try.
        this.state.error = "";
    }

    // Re-run the Returning/New lookup for the corrected number.
    async checkPhone() {
        if (this.state.checking) {
            return;
        }
        this.state.checking = true;
        let result;
        try {
            result = await this.orm.call(
                "laundry.locker.transaction", "pos_check_phone", [this.state.phone]
            );
        } finally {
            this.state.checking = false;
        }
        // A number the shop's rules refuse is not a lookup result, so the
        // fields stay OPEN on it: the cashier is being asked to fix the
        // number, and closing the one input that can be fixed would leave
        // them reading a complaint with nothing to do about it.
        if (result.problem) {
            this.state.error = result.problem;
            this.state.match = null;
            this.state.partnerName = "";
            this.state.partnerLines = [];
            return;
        }
        this.state.error = "";
        this.state.match = result.customer_match;
        this.state.partnerName = result.partner_name || "";
        this.state.partnerLines = result.partner_lines || [];
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

    // The ADDRESS the contact Confirm is about to create will carry - the one
    // thing about it the columns above do not already say (its name and phone
    // are the Customer and Phone cells). It is not a guess: a contact created
    // from a locker drop-off is given the locker as its address when it has
    // none (_apply_locker_address), and a brand-new contact never has one -
    // so this is what will be in the book, shown before it is written rather
    // than found afterwards.
    //
    // Two lines, because that is what the shop writes: the kind of place on
    // the first and the address on the second. Empty when the drop-off has no
    // site, which the template says outright - an order of any service type
    // needs an address on its contact.
    get newContactAddressLines() {
        const site = this.selectedRow ? this.selectedRow.location_name : "";
        if (!site) {
            return [];
        }
        return [site, "LOCKER"];
    }

    // --- the tags box ------------------------------------------------
    //
    // Built here rather than borrowed from the web client's
    // Many2ManyTagsField, which needs a record and a field to hang off and
    // has neither in a POS dialog. What it copies is the SHAPE: chips inside
    // the field, a caret, and a list that narrows as you type - a cashier
    // picking a tag here should be using the control they use on the contact
    // itself, not a row of buttons that happens to do the same job.

    // In the order they were picked, so the box does not reshuffle itself
    // under the cashier's finger as tags go in.
    get selectedTags() {
        return this.state.tagIds
            .map((id) => this.state.tags.find((tag) => tag.id === id))
            .filter((tag) => tag);
    }

    // What is left to pick, narrowed by what has been typed. A tag already
    // in the box is out of the list: it is in the box.
    get tagChoices() {
        const query = this.state.tagQuery.trim().toLowerCase();
        return this.state.tags.filter(
            (tag) =>
                !this.state.tagIds.includes(tag.id) &&
                (!query || (tag.name || "").toLowerCase().includes(query))
        );
    }

    focusTagInput() {
        this.state.tagOpen = true;
        if (this.tagInput.el) {
            this.tagInput.el.focus();
        }
    }

    onTagInput(ev) {
        this.state.tagQuery = ev.target.value;
        this.state.tagOpen = true;
    }

    // Backspace on an empty box takes the last tag back off, the way every
    // other tags field behaves.
    onTagKeydown(ev) {
        if (ev.key === "Backspace" && !this.state.tagQuery && this.state.tagIds.length) {
            this.state.tagIds.pop();
            return;
        }
        if (ev.key === "Enter") {
            ev.preventDefault();
            const first = this.tagChoices[0];
            if (first) {
                this.pickTag(first.id);
            }
        }
        if (ev.key === "Escape") {
            this.state.tagOpen = false;
        }
    }

    // Closed a beat after the box loses focus, so a tap that lands on the
    // list is not cut off by the list going away under it. The list items
    // use mousedown.prevent for the same reason.
    onTagBlur() {
        setTimeout(() => {
            this.state.tagOpen = false;
        }, 150);
    }

    pickTag(tagId) {
        if (!this.state.tagIds.includes(tagId)) {
            this.state.tagIds.push(tagId);
        }
        // The complaint goes with the thing complained about - but only the
        // tag's own complaint: any other refusal on screen is about something
        // this tap did not answer. Emptying the box again does NOT bring the
        // red back - nobody is being refused at that moment - it goes back to
        // the blue "still to do".
        if (this.state.tagRefused) {
            this.state.tagRefused = false;
            this.state.error = "";
        }
        // Cleared so the list opens back up on everything still unpicked -
        // the next tag is rarely spelled like the last one.
        this.state.tagQuery = "";
        this.focusTagInput();
    }

    removeTag(tagId) {
        const at = this.state.tagIds.indexOf(tagId);
        if (at !== -1) {
            this.state.tagIds.splice(at, 1);
        }
    }

    // A contact being CREATED here is being created for a Locker order, and a
    // Locker order requires a tag on its contact (contact_requirements.js).
    // Asked for while the contact is still being made, because the picker is
    // the only place that can ask: once Confirm has run, the tag has to be
    // chased on the contact form.
    //
    // Only when there are tags to pick. A tag list that failed to load must
    // not be a locked Confirm - that would stop the bag being billed over
    // something the cashier cannot do anything about from here.
    get needsTag() {
        return (
            this.state.match === "new" &&
            this.state.tags.length > 0 &&
            this.state.tagIds.length === 0
        );
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
        if (this.needsTag) {
            // The box turns red as well as the line being printed: the
            // sentence sits under the whole screen, and the cashier has to
            // work out which field it is about. Core marks the field itself
            // on a refused save, and this is that same moment.
            this.state.tagRefused = true;
            this.state.error =
                "Pick at least one tag for the new contact \u2014 a Locker order " +
                "needs one.";
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.orm.call(
                "laundry.locker.transaction", "pos_claim", [this.state.selectedId], {
                    phone: this.state.phone,
                    customer_name: this.state.customerName,
                    phone_verified: this.state.verified,
                    // Only ever used on a contact this claim CREATES - see
                    // pos_claim. A returning customer's own tags are not
                    // this screen's business.
                    tag_ids: this.state.tagIds,
                }
            );
            this.props.getPayload(result);
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            throw error;
        }
    }

    // Given the MATCH, not the row: the queue reads it off the row and the
    // chosen drop-off reads it off the state, which is what a correction
    // changes. One function so the two cells cannot label one answer two ways.
    matchLabel(match) {
        return match === "returning" ? "Returning" : "New";
    }

    matchClass(match) {
        return match === "returning" ? "text-bg-success" : "text-bg-warning";
    }
}
