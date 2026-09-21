/** @odoo-module **/

import { Component, useRef, useState, onMounted, onWillUnmount } from "@odoo/owl";

// Must match $laundry-wheel-item-h in new_order_modal.scss — the scroll position is
// read back as an item index, so JS and CSS have to agree on the row height.
const ITEM_H = 44;

function ymd(d) {
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${m}-${day}`;
}

// "Sep 27" — local, field-by-field, so a bare YYYY-MM-DD isn't read as UTC and
// rendered as the previous day here.
export function fmtDateLabel(dateVal) {
    const [y, m, d] = String(dateVal || "").split("-").map(Number);
    if (!y || !m || !d) return dateVal || "";
    return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/**
 * A rolling date picker, sized and coloured like one numpad key.
 *
 * It covers everything past the Today / Tomorrow keys, so the cashier scrolls to a
 * date instead of opening a native date picker. It opens on the day after tomorrow
 * (the most common "not today, not tomorrow" choice) but does NOT select it —
 * rolling or tapping does, which is what tells the parent a date was chosen.
 */
export class DateWheel extends Component {
    static template = "laundry_pos.DateWheel";
    static props = {
        value: { type: String, optional: true },
        onSelect: Function,
        startOffset: { type: Number, optional: true }, // days from today for the first row
        days: { type: Number, optional: true },
    };
    static defaultProps = { value: "", startOffset: 2, days: 60 };

    setup() {
        this.listRef = useRef("list");
        this.dates = this._buildDates();
        this.state = useState({ index: Math.max(0, this._indexOf(this.props.value)) });
        onMounted(() => this._scrollToIndex(this.state.index, false));
        onWillUnmount(() => clearTimeout(this._settle));
    }

    // Day after tomorrow onwards. A value from outside that window (an order being
    // edited, scheduled months out) is spliced in, so the wheel can always show what
    // is actually selected rather than silently disagreeing with it.
    _buildDates() {
        const out = [];
        const today = new Date();
        for (let i = 0; i < this.props.days; i++) {
            const d = new Date(today);
            d.setDate(d.getDate() + this.props.startOffset + i);
            out.push(ymd(d));
        }
        const v = this.props.value;
        if (v && !out.includes(v)) {
            out.push(v);
            out.sort();
        }
        return out.map((value) => ({ value, label: fmtDateLabel(value) }));
    }

    _indexOf(value) {
        return this.dates.findIndex((d) => d.value === value);
    }

    _scrollToIndex(index, smooth) {
        const el = this.listRef.el;
        if (!el) return;
        el.scrollTo({ top: index * ITEM_H, behavior: smooth ? "smooth" : "auto" });
    }

    // The whole key reads as selected whenever the chosen date is one of ours — not
    // just while it sits under the band — so it doesn't flicker grey mid-roll.
    get isActive() {
        return !!this.props.value && this._indexOf(this.props.value) !== -1;
    }

    // Rolling IS choosing. The highlight follows the scroll immediately; the parent
    // is only told once the wheel has settled, so passing over a date doesn't pick it.
    onScroll() {
        const el = this.listRef.el;
        if (!el) return;
        const index = Math.min(
            this.dates.length - 1,
            Math.max(0, Math.round(el.scrollTop / ITEM_H))
        );
        this.state.index = index;
        clearTimeout(this._settle);
        this._settle = setTimeout(() => this.props.onSelect(this.dates[index].value), 150);
    }

    // Tapping a half-visible neighbour is more accurate than flicking on a touchscreen.
    pick(index) {
        this.state.index = index;
        this._scrollToIndex(index, true);
        this.props.onSelect(this.dates[index].value);
    }
}
