/** @odoo-module **/

import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

const { DateTime } = luxon;
const HOURS = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
const MINUTES = [0, 15, 30, 45];
const AMPM = ["AM", "PM"];
const ITEM_H = 40; // keep in sync with .laundry-roller-item height in scss

// Folding date/time picker: one week at a time (no week numbers), week nav arrows, future
// dates disabled, and a roller (flick or tap) for hour / minute (15) / AM-PM. No future.
export class LaundryFoldingPicker extends Component {
    static template = "laundry_pos.LaundryFoldingPicker";
    static components = { Dialog };
    static props = { current: true, onApply: Function, close: Function };

    setup() {
        const base = this.props.current || DateTime.now();
        this.hours = HOURS;
        this.minutes = MINUTES;
        this.ampms = AMPM;
        this.state = useState({
            weekStart: this._weekStart(base),
            sel: base.startOf("day"),
            hour: this._to12(base.hour),
            minute: this._nearest15(base.minute),
            ampm: base.hour < 12 ? "AM" : "PM",
            error: "",
        });
        this.hourRoller = useRef("hourRoller");
        this.minRoller = useRef("minRoller");
        this.ampmRoller = useRef("ampmRoller");
        onMounted(() => {
            this._scrollTo(this.hourRoller.el, this.hours.indexOf(this.state.hour));
            this._scrollTo(this.minRoller.el, this.minutes.indexOf(this.state.minute));
            this._scrollTo(this.ampmRoller.el, this.ampms.indexOf(this.state.ampm));
        });
    }

    _weekStart(dt) {
        // Sunday-start week (luxon weekday: Mon=1..Sun=7).
        return dt.startOf("day").minus({ days: dt.weekday % 7 });
    }
    _to12(h) {
        return ((h + 11) % 12) + 1;
    }
    _nearest15(m) {
        return MINUTES.reduce((a, b) => (Math.abs(b - m) < Math.abs(a - m) ? b : a), 0);
    }
    _scrollTo(el, idx) {
        if (el && idx >= 0) {
            el.scrollTop = idx * ITEM_H;
        }
    }

    get monthLabel() {
        return this.state.weekStart.toFormat("MMMM yyyy");
    }
    get weekDays() {
        const today = DateTime.now().startOf("day");
        const days = [];
        for (let i = 0; i < 7; i++) {
            const d = this.state.weekStart.plus({ days: i });
            days.push({
                dt: d,
                label: d.day,
                dow: d.toFormat("ccc"),
                isToday: d.hasSame(today, "day"),
                isSel: d.hasSame(this.state.sel, "day"),
                isFuture: d > today,
            });
        }
        return days;
    }

    prevWeek() {
        this.state.weekStart = this.state.weekStart.minus({ weeks: 1 });
    }
    nextWeek() {
        this.state.weekStart = this.state.weekStart.plus({ weeks: 1 });
    }
    selectDay(d) {
        if (d.isFuture) {
            return;
        }
        this.state.sel = d.dt;
        this.state.error = "";
    }

    _onRoll(kind, ev) {
        clearTimeout(this._t);
        const el = ev.target;
        this._t = setTimeout(() => {
            const idx = Math.round(el.scrollTop / ITEM_H);
            const list = kind === "hour" ? this.hours : kind === "minute" ? this.minutes : this.ampms;
            const val = list[Math.max(0, Math.min(idx, list.length - 1))];
            if (kind === "hour") this.state.hour = val;
            else if (kind === "minute") this.state.minute = val;
            else this.state.ampm = val;
        }, 120);
    }
    setHour(h) {
        this.state.hour = h;
        this._scrollTo(this.hourRoller.el, this.hours.indexOf(h));
    }
    setMinute(m) {
        this.state.minute = m;
        this._scrollTo(this.minRoller.el, this.minutes.indexOf(m));
    }
    setAmpm(a) {
        this.state.ampm = a;
        this._scrollTo(this.ampmRoller.el, this.ampms.indexOf(a));
    }

    get result() {
        let h = this.state.hour % 12;
        if (this.state.ampm === "PM") {
            h += 12;
        }
        return this.state.sel.set({ hour: h, minute: this.state.minute, second: 0, millisecond: 0 });
    }
    apply() {
        if (this.result > DateTime.now()) {
            this.state.error = "Processed date cannot be in the future.";
            return;
        }
        this.props.onApply(this.result);
        this.props.close();
    }
}
