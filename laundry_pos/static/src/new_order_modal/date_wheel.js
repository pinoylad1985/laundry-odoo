/** @odoo-module **/

import { Component, useRef, onMounted, onWillUpdateProps, onWillUnmount } from "@odoo/owl";

// Must match $laundry-wheel-item-h in new_order_modal.scss — the scroll position is
// read back as an item index, so JS and CSS have to agree on the row height.
const ITEM_H = 44;

function ymd(d) {
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${m}-${day}`;
}

function dayFromToday(offset) {
    const d = new Date();
    d.setDate(d.getDate() + offset);
    return ymd(d);
}

// "Sep 27" — local, field-by-field, so a bare YYYY-MM-DD isn't read as UTC and
// rendered as the previous day here.
export function fmtDateLabel(dateVal) {
    const [y, m, d] = String(dateVal || "").split("-").map(Number);
    if (!y || !m || !d) return dateVal || "";
    return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/**
 * A rolling date picker, sized like one numpad key.
 *
 * It spans the last 5 days through two months out, so a backdated order and a
 * far-out booking are both reachable without leaving the modal. It opens on the day
 * after tomorrow (the most common "not today, not tomorrow" choice) but does NOT
 * select it — rolling or tapping does, which is what tells the parent a date was chosen.
 */
export class DateWheel extends Component {
    static template = "laundry_pos.DateWheel";
    static props = {
        value: { type: String, optional: true },
        onSelect: Function,
        startOffset: { type: Number, optional: true }, // days from today for the first row
        days: { type: Number, optional: true },
    };
    // -5 … +61: the previous 5 days, then today onward.
    static defaultProps = { value: "", startOffset: -5, days: 67 };

    setup() {
        this.listRef = useRef("list");
        this.today = dayFromToday(0);
        this.tomorrow = dayFromToday(1);
        this.dates = this._buildDates();
        // Plain property, not `useState` — the highlight follows the SELECTED date
        // (a prop), so tracking the scroll position doesn't need to re-render.
        this.index = this._initialIndex();
        onMounted(() => this._scrollToIndex(this.index, false));
        // A date picked on the Today / Tomorrow keys is one of ours too, so roll to it
        // — otherwise the selected row would be highlighted somewhere off-screen.
        onWillUpdateProps((next) => {
            if (next.value !== this.props.value) {
                const i = this._indexOf(next.value);
                if (i !== -1 && i !== this.index) {
                    this.index = i;
                    this._scrollToIndex(i, true);
                }
            }
        });
        onWillUnmount(() => clearTimeout(this._settle));
    }

    // A value from outside the window (an order scheduled months out) is spliced in,
    // so the wheel can always show what is actually selected rather than silently
    // disagreeing with it.
    _buildDates() {
        const out = [];
        for (let i = 0; i < this.props.days; i++) {
            out.push(dayFromToday(this.props.startOffset + i));
        }
        const v = this.props.value;
        if (v && !out.includes(v)) {
            out.push(v);
            out.sort();
        }
        return out.map((value) => ({
            value,
            label: fmtDateLabel(value),
            note: value === this.today ? "(Today)" : value === this.tomorrow ? "(Tomorrow)" : "",
        }));
    }

    // Open on the selection when there is one, else on the day after tomorrow.
    _initialIndex() {
        const picked = this._indexOf(this.props.value);
        if (picked !== -1) return picked;
        return Math.max(0, this._indexOf(dayFromToday(2)));
    }

    /**
     * Whether the whole key reads as selected.
     *
     * True when the chosen date is one of ours — not just while it sits under the
     * band — so the key doesn't flicker grey mid-roll. Today and tomorrow are the
     * exception: they have their own keys, which go primary themselves, and two
     * solid keys claiming one selection is a contradiction. The row inside the wheel
     * still highlights for those, so the wheel never looks out of step either.
     */
    get isActive() {
        const v = this.props.value;
        return !!v && v !== this.today && v !== this.tomorrow && this._indexOf(v) !== -1;
    }

    _indexOf(value) {
        return this.dates.findIndex((d) => d.value === value);
    }

    _scrollToIndex(index, smooth) {
        const el = this.listRef.el;
        if (!el) return;
        el.scrollTo({ top: index * ITEM_H, behavior: smooth ? "smooth" : "auto" });
    }

    // Rolling IS choosing, but the parent is only told once the wheel has settled, so
    // passing over a date doesn't pick it. Nothing highlights until then — the only
    // highlighted row is the selected one.
    onScroll() {
        const el = this.listRef.el;
        if (!el) return;
        const index = Math.min(
            this.dates.length - 1,
            Math.max(0, Math.round(el.scrollTop / ITEM_H))
        );
        this.index = index;
        clearTimeout(this._settle);
        this._settle = setTimeout(() => this.props.onSelect(this.dates[index].value), 150);
    }

    // Tapping a half-visible neighbour is more accurate than flicking on a touchscreen.
    pick(index) {
        this.index = index;
        this._scrollToIndex(index, true);
        this.props.onSelect(this.dates[index].value);
    }
}
