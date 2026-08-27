/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Reprint picker: choose WHICH receipt copies to print instead of always
 * reprinting the full set (see PosStore.printReceipt). Nothing is pre-ticked —
 * the cashier selects exactly what they need, so a stray tap prints nothing.
 *
 * Rows flagged `disabled` (e.g. CUSTOMER COPY on a service type that has none)
 * are shown greyed with the reason, never selectable.
 */
export class ReprintCopiesPopup extends Component {
    static template = "laundry_pos.ReprintCopiesPopup";
    static components = { Dialog };
    static props = {
        close: Function,
        getPayload: Function,
        copies: Array, // [{ label, boxedUuid, disabled?, reason? }]
    };

    setup() {
        this.state = useState({ checked: this.props.copies.map(() => false) });
    }

    get selectable() {
        return this.props.copies.filter((c) => !c.disabled);
    }

    get selectedCount() {
        return this.state.checked.filter(Boolean).length;
    }

    get allSelected() {
        return this.selectable.length > 0 && this.selectedCount === this.selectable.length;
    }

    toggle(index) {
        if (this.props.copies[index]?.disabled) {
            return;
        }
        this.state.checked[index] = !this.state.checked[index];
    }

    toggleAll() {
        const next = !this.allSelected;
        this.state.checked = this.props.copies.map((c) => (c.disabled ? false : next));
    }

    confirm() {
        if (!this.selectedCount) {
            return;
        }
        this.props.getPayload(this.props.copies.filter((c, i) => this.state.checked[i]));
        this.props.close();
    }
}
