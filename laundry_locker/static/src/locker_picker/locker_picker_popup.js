/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

// The unbilled locker queue, shown when LOCKER is picked in the New Order modal.
// Picking a row hands its customer back to the modal; the row is marked billed
// server-side at that moment so a second till can't take the same drop-off.
//
// The phone number is editable here on purpose: locker customers key their own
// number into the PudoPro screen and do get it wrong, and a wrong number both
// misses the returning-customer match AND would create a contact nobody can be
// reached on. So a row with no match cannot be confirmed until someone has
// called the number and heard it ring.
export class LockerPickerPopup extends Component {
    static template = "laundry_locker.LockerPickerPopup";
    static components = { Dialog };
    static props = {
        close: Function,
        getPayload: Function,
        // Set to re-open on a transaction this till already took, to correct
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
            verified: false,
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
                row.new_laundry_at,
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
        this.state.verified = !!row.phone_verified;
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
        } finally {
            this.state.checking = false;
        }
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
        if (this.needsVerification) {
            this.state.error =
                "No customer has this number. Call it, confirm it rings, then tick the box below.";
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.orm.call(
                "laundry.locker.transaction", "pos_claim", [this.state.selectedId], {
                    phone: this.state.phone,
                    customer_name: this.state.customerName,
                    phone_verified: this.state.verified,
                    recheck: this.isRecheck,
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
