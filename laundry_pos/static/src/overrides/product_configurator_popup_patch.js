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
    laundryIsBgcOrder,
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
    // Asked of core's OWN `selectedValues` - the very list it is about to submit -
    // rather than of a `state.attributes` key, because core has changed that key
    // between 19.0 builds (see _laundryKeyMode). A line counts as answered when one
    // of its values is in that list, which is true however the state is keyed.
    get laundryAllAttributesSelected() {
        const chosen = new Set((this.selectedValues || []).map((v) => v?.id));
        return (this.validAttributeLineIds || []).every((line) => {
            if (isTurnaround(line)) return true; // turnaround is auto-set + locked
            return (line.product_template_value_ids || []).some((v) => chosen.has(v.id));
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
     * WHICH KEY does this core build use for `state.attributes`?
     *
     * Odoo 19.0-20261123 and later key it by the attribute LINE's own id; the
     * 19.0 builds before that keyed it by the line's `attribute_id`. Every
     * pre-select and every clear in this file writes into that object, so getting
     * the key wrong silently does nothing - which is exactly how it failed: core's
     * own first-value pre-selection was never cleared, AND the "all options
     * selected" check read an empty key, so the popup showed every option ticked
     * and still refused to Add.
     *
     * So it is PROBED, not assumed: core's setup() seeds exactly one entry per
     * line, so whichever spelling of the line ids accounts for all the seeded keys
     * is the one this build uses. Settled once, before anything of ours adds a key.
     */
    _laundryKeyMode() {
        if (this._laundryKeyModeCache) {
            return this._laundryKeyModeCache;
        }
        const lines = this.props.productTemplate?.attribute_line_ids || [];
        const keys = new Set(Object.keys(this.state?.attributes || {}));
        const seeded = (ids) => ids.length > 0 && ids.every((id) => id != null && keys.has(String(id)));
        const byLine = seeded(lines.map((l) => l?.id));
        const byAttr = seeded(lines.map((l) => l?.attribute_id?.id));
        // Current core wins a tie, and is the fallback when neither matches.
        this._laundryKeyModeCache = byAttr && !byLine ? "attr" : "line";
        return this._laundryKeyModeCache;
    },

    _laundryStateKey(line) {
        return this._laundryKeyMode() === "attr" ? line?.attribute_id?.id : line?.id;
    },

    // Set a line's selection whichever way this build keys the state.
    _laundrySetSelected(line, selected) {
        const key = this._laundryStateKey(line);
        if (key == null) {
            return;
        }
        if (!this.state.attributes[key]) {
            this.state.attributes[key] = { selected: [], custom_value: "" };
        }
        this.state.attributes[key].selected = selected;
    },

    /**
     * Guard against a core crash: setup() seeds `state.attributes` with one entry
     * per attribute LINE, but initAttributes() then assigns `.selected` using a key
     * read off each VALUE (its `attribute_line_id` on current core, its
     * `attribute_id` on older builds). If a product has a value pointing at
     * something that isn't one of the seeded lines - a malformed/mismigrated
     * product, e.g. after a DB restore - core throws "Cannot set properties of
     * undefined (setting 'selected')" and the whole popup dies. We pre-create an
     * entry for BOTH spellings of every value's key first, so the assignment always
     * lands somewhere instead of crashing.
     */
    initAttributes() {
        // Settle the keying BEFORE the guard below adds any entries - the probe reads
        // the keys core's own setup() seeded, and a stray key would make it lie.
        this._laundryKeyMode();
        for (const line of this.props.productTemplate?.attribute_line_ids || []) {
            for (const value of line.product_template_value_ids || []) {
                for (const key of [value.attribute_id?.id, value.attribute_line_id?.id]) {
                    if (key != null && !this.state.attributes[key]) {
                        this.state.attributes[key] = { selected: [], custom_value: "" };
                    }
                }
            }
        }
        const res = super.initAttributes(...arguments);
        // Core opens with the FIRST value of every attribute already ticked. On a
        // laundry service that means a size or a type nobody chose can ride onto the
        // line - and onto the price - just by pressing Add, and it looks exactly like
        // a deliberate choice afterwards. So nothing is pre-selected: the cashier has
        // to pick each option. `confirm()` already refuses to Add until they all are,
        // and the template says why, so clearing these creates no dead end.
        // Same reasoning as the New Order modal's date strip, which opens on today
        // without selecting it.
        // TURNAROUND IS EXEMPT - it is set by the order's schedule, not chosen here
        // (it is rendered locked), and _laundryPreselectTat fills it right after this.
        if (this.isLaundryService) {
            for (const line of this.props.productTemplate?.attribute_line_ids || []) {
                if (isTurnaround(line)) {
                    continue;
                }
                this._laundrySetSelected(line, []); // the empty shape core's setup() uses
            }
        }
        return res;
    },

    // Pre-select each attribute value the line already has, so re-opening the
    // configurator shows the previous selection rather than a blank form.
    _laundryPreselectEdit() {
        if (!editSelectionIds) {
            return;
        }
        for (const line of this.props.productTemplate?.attribute_line_ids || []) {
            const match = (line.product_template_value_ids || []).find((v) =>
                editSelectionIds.includes(v.id)
            );
            if (match) {
                this._laundrySetSelected(line, match);
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
            if (match) {
                this._laundrySetSelected(line, match);
            }
        }
    },

    // Express and BGC pricing: a value charges the one price that applies to this
    // order's area and turnaround, instead of its Extra Price. Overriding core's
    // getter covers BOTH the modal's header total and computePayload().price_extra
    // — the latter is what the product-grid add-flow bills. (The cart-tap path
    // prices itself through buildConfiguredLineVals, which applies the same rule.)
    get priceExtra() {
        const isExpress = laundryIsExpressSelection(this.selectedValues);
        const isBgc = laundryIsBgcOrder(this.pos?.getOrder?.());
        return this.selectedValues
            .filter((value) => value.attribute_id.create_variant === "no_variant")
            .reduce((acc, val) => acc + laundryEffectiveExtra(val, isExpress, isBgc), 0);
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
// on an express order that's the value's Express Price, on a BGC order its BGC one.
// `allSelectedValues` carries the whole current selection, so each option can see
// the chosen turnaround. ⚠ this component has no `this.pos` of its own (unlike the
// popup above), so the order is read off the service.
patch(BaseProductAttribute.prototype, {
    laundryValueExtra(value) {
        return laundryEffectiveExtra(
            value,
            laundryIsExpressSelection(this.props.allSelectedValues || []),
            laundryIsBgcOrder(this.env?.services?.pos?.getOrder?.())
        );
    },
});
