/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useState, useRef } from "@odoo/owl";
import {
    ProductConfiguratorPopup,
    BaseProductAttribute,
} from "@point_of_sale/app/components/popups/product_configurator_popup/product_configurator_popup";
import {
    laundryCodeForProduct,
    wdfRoundedKg,
    laundryIsExpressSelection,
    laundryEffectiveExtra,
} from "@laundry_pos/utils/laundry_products";

function isTurnaround(attrLine) {
    return String(attrLine?.attribute_id?.name || "").startsWith("Turnaround");
}

// PTAV ids of the line currently being re-configured, so the configurator opens
// pre-filled with what was previously selected (set by order_summary_patch).
let editSelectionIds = null;
export function setEditSelection(ids) {
    editSelectionIds = ids && ids.length ? ids : null;
}

// Current weight (kg) of the line being re-configured, so the Wash-Dry-Fold weight
// input opens pre-filled. Set by order_summary_patch before opening the configurator.
let editWeightKg = null;
export function setEditWeight(kg) {
    editWeightKg = kg || null;
}

// Weight from the last WDF configurator confirm. PosStore.addLineToCurrentOrder reads
// this to apply the weight even when the line was configured by the CORE add-flow
// (adding a WDF from the product grid), which ignores our custom payload fields.
let lastWdfWeight = null;
export function consumeWdfWeight() {
    const v = lastWdfWeight;
    lastWdfWeight = null;
    return v;
}

// Customer note pre-fill (on re-open) + last-confirm stash (applied by the add-flow).
let editNote = null;
export function setEditNote(note) {
    editNote = note || null;
}
let lastLaundryNote = null;
export function consumeLaundryNote() {
    const v = lastLaundryNote;
    lastLaundryNote = null;
    return v;
}

patch(ProductConfiguratorPopup.prototype, {
    setup() {
        super.setup();
        // Wash-Dry-Fold weight input — pre-filled with the line's current weight on re-open.
        this.laundryKgState = useState({
            kg: editWeightKg != null ? String(editWeightKg) : "",
            invalid: false,
        });
        this.laundryKgRef = useRef("laundryWeightInput");
        // Customer note input — pre-filled with the line's note on re-open.
        this.laundryNoteState = useState({ note: editNote || "" });
        this._laundryPreselectEdit(); // restore the line's previous choices
        this._laundryPreselectTat(); // turnaround follows the order's TAT
    },

    // True when configuring a Wash-Dry-Fold product (drives the KG weight input).
    get isLaundryWdf() {
        return laundryCodeForProduct(this.props.productTemplate) === "wdf";
    },

    // True for any of the 5 laundry services (drives the Note box + required-attrs rule).
    get isLaundryService() {
        return !!laundryCodeForProduct(this.props.productTemplate);
    },

    onLaundryNoteInput(ev) {
        this.laundryNoteState.note = ev.target.value;
    },

    // Every attribute must have a selection before Add (laundry services only).
    get laundryAllAttributesSelected() {
        return (this.validAttributeLineIds || []).every((line) => {
            if (isTurnaround(line)) return true; // turnaround is auto-set + locked
            const sel = this.state.attributes?.[line.attribute_id?.id]?.selected;
            return sel && !(Array.isArray(sel) && sel.length === 0);
        });
    },

    // The weight currently in the input, read straight from the DOM so it's reliable
    // at confirm time (avoids a reactive-state timing miss on the first open).
    get laundryEnteredKg() {
        const el = this.laundryKgRef?.el;
        return parseFloat(el ? el.value : this.laundryKgState.kg);
    },

    onLaundryWeightInput(ev) {
        this.laundryKgState.kg = ev.target.value;
        this.laundryKgState.invalid = false;
    },

    // The entered weight rounded to a WHOLE KG (decimal above 0.40 rounds up; 0 if
    // blank/invalid). The per-line minimum is applied later by wdfBilledQty.
    get laundryRoundedKg() {
        const kg = this.laundryEnteredKg;
        if (!(kg > 0)) return 0;
        return wdfRoundedKg(kg);
    },

    // Actual Weight is REQUIRED for Wash-Dry-Fold — block Add without a valid value.
    confirm() {
        // Laundry services: every attribute must be selected before Add.
        if (this.isLaundryService && !this.laundryAllAttributesSelected) {
            return;
        }
        if (this.isLaundryWdf) {
            // Require a real weighed value: > 0 with EXACTLY 2 decimal places (e.g. 3.25),
            // so a proper scale reading is entered (not a rounded/placeholder number).
            const raw = String(this.laundryKgRef?.el?.value ?? "").trim();
            if (!(this.laundryEnteredKg > 0) || !/^\d+\.\d{2}$/.test(raw)) {
                this.laundryKgState.invalid = true;
                this.laundryKgRef?.el?.focus?.();
                return;
            }
        }
        if (this.isLaundryWdf) {
            // Stash for the product-grid add-flow (see PosStore.addLineToCurrentOrder).
            lastWdfWeight = this.laundryEnteredKg;
        }
        if (this.isLaundryService) {
            lastLaundryNote = (this.laundryNoteState.note || "").toUpperCase();
        }
        return super.confirm(...arguments);
    },

    // Ride the weight along in the configurator payload: the ACTUAL entered value
    // (shown as-is, like an attribute) plus the rounded value that becomes the qty.
    computePayload() {
        const payload = super.computePayload();
        const actual = this.laundryEnteredKg;
        if (this.isLaundryWdf && actual > 0) {
            payload.laundryActualWeight = actual;            // entered value, shown as-is
            payload.laundryWeightKg = this.laundryRoundedKg; // rounded up to 0.5 → billed qty
        }
        if (this.isLaundryService) {
            payload.laundryNote = (this.laundryNoteState.note || "").toUpperCase();
        }
        return payload;
    },

    /**
     * Guard against a core crash: setup() seeds `state.attributes` keyed by each
     * attribute LINE's attribute_id, but initAttributes() then sets `.selected`
     * on `state.attributes[value.attribute_id.id]` — keyed by each VALUE's
     * attribute_id. If a product has a value whose attribute_id isn't one of the
     * line keys (a malformed/mismigrated product, e.g. after a DB restore), core
     * throws "Cannot set properties of undefined (setting 'selected')". We
     * pre-create a state entry for every value's attribute_id first, so the
     * assignment always lands somewhere instead of crashing the whole popup.
     */
    initAttributes() {
        for (const line of this.props.productTemplate?.attribute_line_ids || []) {
            for (const value of line.product_template_value_ids || []) {
                const attrId = value.attribute_id?.id;
                if (attrId != null && !this.state.attributes[attrId]) {
                    this.state.attributes[attrId] = { selected: [], custom_value: "" };
                }
            }
        }
        return super.initAttributes(...arguments);
    },

    // Pre-select each attribute value the line already has, so re-opening the
    // configurator shows the previous selection rather than a blank form.
    _laundryPreselectEdit() {
        if (!editSelectionIds) {
            return;
        }
        for (const line of this.props.productTemplate?.attribute_line_ids || []) {
            const attrId = line.attribute_id?.id;
            const match = (line.product_template_value_ids || []).find((v) =>
                editSelectionIds.includes(v.id)
            );
            if (match && this.state?.attributes?.[attrId]) {
                this.state.attributes[attrId].selected = match;
            }
        }
    },

    // Pre-select the turnaround value matching the order's TAT (locked anyway).
    _laundryPreselectTat() {
        const tat = this.pos?.getOrder?.()?.laundry_turnaround;
        if (!tat) {
            return;
        }
        for (const line of this.props.productTemplate?.attribute_line_ids || []) {
            if (!isTurnaround(line)) {
                continue;
            }
            const vals = line.product_template_value_ids || [];
            const match = vals.find(
                (v) => String(v.name || "").toLowerCase().includes("express") === (tat === "express")
            );
            const attrId = line.attribute_id?.id;
            if (match && this.state?.attributes?.[attrId]) {
                this.state.attributes[attrId].selected = match;
            }
        }
    },

    // Express pricing: a value with an Express Price charges THAT instead of its
    // Extra Price when the turnaround is EXPRESS. Overriding core's getter covers
    // BOTH the modal's header total and computePayload().price_extra — the latter is
    // what the product-grid add-flow bills. (The cart-tap path prices itself through
    // buildConfiguredLineVals, which applies the same rule.)
    get priceExtra() {
        const isExpress = laundryIsExpressSelection(this.selectedValues);
        return this.selectedValues
            .filter((value) => value.attribute_id.create_variant === "no_variant")
            .reduce((acc, val) => acc + laundryEffectiveExtra(val, isExpress), 0);
    },

    // Used by the template to grey out / lock the turnaround attribute.
    isTurnaroundAttr(attrLine) {
        return isTurnaround(attrLine);
    },

    // True when this product has a turnaround attribute (drives the note).
    get hasLaundryTurnaround() {
        return this.validAttributeLineIds.some((a) => isTurnaround(a));
    },
});

// The price pill next to each option shows the price that will actually be charged:
// on an express order that's the value's Express Price. `allSelectedValues` carries
// the whole current selection, so each option can see the chosen turnaround.
patch(BaseProductAttribute.prototype, {
    laundryValueExtra(value) {
        return laundryEffectiveExtra(
            value,
            laundryIsExpressSelection(this.props.allSelectedValues || [])
        );
    },
});
