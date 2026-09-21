/** @odoo-module **/

import { Component, useRef, onMounted, onWillUpdateProps, onWillUnmount } from "@odoo/owl";

// Must match $laundry-wheel-item-w in new_order_modal.scss — the scroll position is
// read back as an item index, so JS and CSS have to agree on the column width.
const ITEM_W = 120;

function ymd(d) {
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${m}-${day}`;
}

export function dayFromToday(offset) {
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
 * The whole date picker for one schedule field: a strip of dates you flick
 * SIDEWAYS, one key wide.
 *
 * It spans the last 5 days through two months out, so a backdated order and a far
 * booking are both reachable without leaving the modal. There are no separate
 * Today / Tomorrow keys — those two dates are items in here like any other, marked
 * with a note. Every field OPENS centred on today, but nothing is pre-selected.
 */
export class DateWheel extends Component {
    static template = "laundry_pos.DateWheel";
    static props = {
        value: { type: String, optional: true },
        onSelect: Function,
        startOffset: { type: Number, optional: true }, // days from today for the first item
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
        // A date set from outside (an order being edited) has to roll into view, or
        // the highlighted item would sit off-screen and the strip would look empty.
        onWillUpdateProps((next) => {
            if (next.value !== this.props.value) {
                const i = this._indexOf(next.value);
                if (i !== -1 && i !== this.index) {
                    this.index = i;
                    this._scrollToIndex(i, true);
                }
            }
        });
        onWillUnmount(() => {
            clearTimeout(this._settle);
            clearTimeout(this._unmute);
        });
    }

    // A value from outside the window (an order scheduled months out) is spliced in,
    // so the strip can always show what is actually selected rather than silently
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

    // Open on the selection if there is one, else on today. Opening there is NOT
    // selecting: the cashier still has to pick, so a schedule can't be confirmed
    // without having been looked at.
    _initialIndex() {
        const picked = this._indexOf(this.props.value);
        if (picked !== -1) return picked;
        return Math.max(0, this._indexOf(this.today));
    }

    _indexOf(value) {
        return this.dates.findIndex((d) => d.value === value);
    }

    // Scrolling the strip ourselves must never count as choosing. The scroll handler
    // can't tell a programmatic scroll from a flick, so we mute selection until the
    // scroll it triggers has played out — otherwise opening the modal would select
    // whatever date the strip happens to open on.
    _scrollToIndex(index, smooth) {
        const el = this.listRef.el;
        if (!el) return;
        this._muted = true;
        clearTimeout(this._unmute);
        this._unmute = setTimeout(() => (this._muted = false), smooth ? 600 : 100);
        el.scrollTo({ left: index * ITEM_W, behavior: smooth ? "smooth" : "auto" });
    }

    // Any touch on the strip is the cashier taking over, so stop muting immediately
    // rather than waiting out a scroll they've just interrupted.
    onPointerDown() {
        clearTimeout(this._unmute);
        this._muted = false;
    }

    // Flicking IS choosing, but the parent is only told once the strip has settled,
    // so sliding past a date doesn't pick it.
    onScroll() {
        const el = this.listRef.el;
        if (!el) return;
        const index = Math.min(
            this.dates.length - 1,
            Math.max(0, Math.round(el.scrollLeft / ITEM_W))
        );
        this.index = index;
        if (this._muted) return;
        clearTimeout(this._settle);
        this._settle = setTimeout(() => this.props.onSelect(this.dates[index].value), 150);
    }

    // Tapping a neighbour is more accurate than flicking on a touchscreen.
    pick(index) {
        this.index = index;
        this._scrollToIndex(index, true);
        this.props.onSelect(this.dates[index].value);
    }
}
