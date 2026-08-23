import { browser } from "@web/core/browser/browser";
import { AccountReport } from "@account_reports/components/account_report/account_report";
import { AccountReportHeader } from "@account_reports/components/account_report/header/header";

import { onMounted, onWillUnmount } from "@odoo/owl";

// A column width is a personal display preference, not part of the report, so it
// is kept in the browser rather than in the options (which travel through the
// session and the export).
const STORAGE_PREFIX = "laundry_account_reports.column_widths.";

// Key used for the leftmost (customer / line name) column, which has no
// expression label. The '#' keeps it from ever colliding with a real one.
const NAME_COLUMN = "#name";

const MIN_WIDTH = 48;

/**
 * Table header of the Aged Receivable (Detailed) report, with a drag handle on
 * the right edge of every column.
 *
 * The table keeps its automatic layout: a width on its own would only be a hint
 * the browser is free to ignore, so a pinned column also gets min/max-width and
 * clipped contents (see laundry_aged_receivable.scss), which brings its minimum
 * content width down to the pinned value. The very first drag pins every column
 * at its current width, so nothing jumps; from then on only the dragged column
 * moves. Double-clicking any handle clears the lot.
 */
export class LaundryAgedReceivableHeader extends AccountReportHeader {
    static template = "laundry_account_reports.LaundryAgedReceivableHeader";

    setup() {
        super.setup();

        this.styleElement = null;
        this.widths = this.loadWidths();

        onMounted(() => this.applyWidths());
        onWillUnmount(() => {
            this.styleElement?.remove();
            this.styleElement = null;
        });
    }

    // -----------------------------------------------------------------------------------------------------------------
    // Storage
    // -----------------------------------------------------------------------------------------------------------------
    get storageKey() {
        return `${STORAGE_PREFIX}${this.controller.options.report_id}`;
    }

    loadWidths() {
        try {
            const stored = JSON.parse(browser.localStorage.getItem(this.storageKey));

            return stored && typeof stored === "object" ? stored : {};
        } catch {
            return {};
        }
    }

    saveWidths() {
        try {
            if (Object.keys(this.widths).length) {
                browser.localStorage.setItem(this.storageKey, JSON.stringify(this.widths));
            } else {
                browser.localStorage.removeItem(this.storageKey);
            }
        } catch {
            // Private browsing or a full quota: resizing still works, it just
            // will not survive a reload. Not worth interrupting the user for.
        }
    }

    // -----------------------------------------------------------------------------------------------------------------
    // Applying the widths
    // -----------------------------------------------------------------------------------------------------------------
    /**
     * Widths are applied as a stylesheet rather than as inline styles: the cells
     * are re-rendered by Owl on every fold, sort and reload, and a selector on
     * data-expression_label keeps applying through all of that.
     */
    applyWidths() {
        const entries = Object.entries(this.widths);

        if (!entries.length) {
            this.styleElement?.remove();
            this.styleElement = null;

            return;
        }

        if (!this.styleElement) {
            this.styleElement = document.createElement("style");
            document.head.appendChild(this.styleElement);
        }

        const scope = ".account_report.laundry_aged_receivable table";
        const rules = [];

        for (const [label, width] of entries) {
            let target;

            if (label === NAME_COLUMN) {
                target = `${scope} > thead > tr > th:first-child, ${scope} > tbody > tr > td.line_name`;
            } else if (/^\w+$/.test(label)) {
                target = `${scope} th[data-expression_label="${label}"], ${scope} td[data-expression_label="${label}"]`;
            } else {
                continue; // Not an expression label we wrote; do not build a selector out of it.
            }

            rules.push(`${target} { width: ${width}px; min-width: ${width}px; max-width: ${width}px; }`);
        }

        this.styleElement.textContent = rules.join("\n");
    }

    // -----------------------------------------------------------------------------------------------------------------
    // Dragging
    // -----------------------------------------------------------------------------------------------------------------
    /** Freeze every column at the width it currently has, so the first drag moves one column and not all of them. */
    pinCurrentWidths(table) {
        const row = table.querySelector('tr[data-id="column_subheaders_row"]');

        if (!row) {
            return;
        }

        for (const header of row.querySelectorAll("th[data-expression_label]")) {
            const label = header.dataset.expression_label;

            if (label) {
                this.widths[label] = Math.round(header.getBoundingClientRect().width);
            }
        }

        const nameHeader = row.querySelector("th:first-child");

        if (nameHeader) {
            this.widths[NAME_COLUMN] = Math.round(nameHeader.getBoundingClientRect().width);
        }
    }

    startResize(ev, expressionLabel) {
        const table = ev.target.closest("table");

        if (!table || !expressionLabel) {
            return;
        }

        ev.preventDefault();
        ev.stopPropagation(); // The header cell is also the sort button.

        if (!Object.keys(this.widths).length) {
            this.pinCurrentWidths(table);
        }

        const startX = ev.clientX;
        const startWidth = this.widths[expressionLabel] ?? MIN_WIDTH;

        const onMouseMove = (moveEv) => {
            this.widths[expressionLabel] = Math.max(
                MIN_WIDTH,
                Math.round(startWidth + moveEv.clientX - startX)
            );
            this.applyWidths();
        };

        const onMouseUp = () => {
            document.removeEventListener("mousemove", onMouseMove);
            document.removeEventListener("mouseup", onMouseUp);
            document.body.classList.remove("laundry_resizing_column");
            this.saveWidths();
        };

        document.body.classList.add("laundry_resizing_column");
        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
    }

    resetWidths() {
        this.widths = {};
        this.applyWidths();
        this.saveWidths();
    }
}

AccountReport.registerCustomComponent(LaundryAgedReceivableHeader);
