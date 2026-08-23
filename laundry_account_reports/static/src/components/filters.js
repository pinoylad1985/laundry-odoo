import { _t } from "@web/core/l10n/translation";
import { AccountReport } from "@account_reports/components/account_report/account_report";
import { AccountReportFilters } from "@account_reports/components/account_report/filters/filters";

/**
 * Filter bar of the Aged Receivable (Detailed) report.
 *
 * Adds a Service Type filter (tick the types you want, and/or type a word the
 * type must - or must not - contain) and an Address filter (same contains /
 * does not contain text box). Everything it does is write to the report options;
 * the matching itself happens server side, in laundry_aged_receivable.py, so the
 * customer subtotals and the aging buckets stay consistent with the rows shown.
 *
 * Option keys must stay in step with SERVICE_TYPE_OPTION & co. in
 * models/laundry_aged_receivable.py.
 */
export class LaundryAgedReceivableFilters extends AccountReportFilters {
    static template = "laundry_account_reports.LaundryAgedReceivableFilters";

    // -----------------------------------------------------------------------------------------------------------------
    // Service type
    // -----------------------------------------------------------------------------------------------------------------
    get serviceTypes() {
        return this.controller.cachedFilterOptions.laundry_service_types || [];
    }

    /** Short recap shown on the closed dropdown, so an active filter is visible without opening it. */
    get serviceTypeSummary() {
        const ticked = this.serviceTypes.filter((serviceType) => serviceType.selected);
        const parts = [];

        if (ticked.length === 1) {
            parts.push(ticked[0].name);
        } else if (ticked.length > 1) {
            parts.push(_t("%s selected", ticked.length));
        }

        const summary = this.textSummary("laundry_service_type_text", "laundry_service_type_mode");
        if (summary) {
            parts.push(summary);
        }

        return parts.join(", ");
    }

    get addressSummary() {
        return this.textSummary("laundry_address_text", "laundry_address_mode");
    }

    textSummary(textKey, modeKey) {
        const options = this.controller.cachedFilterOptions;
        const text = (options[textKey] || "").trim();

        if (!text) {
            return "";
        }

        return options[modeKey] === "not_contains" ? _t('not "%s"', text) : `"${text}"`;
    }

    // -----------------------------------------------------------------------------------------------------------------
    // Actions
    // -----------------------------------------------------------------------------------------------------------------
    async toggleServiceType(index) {
        await this.filterClicked({
            optionKey: `laundry_service_types.${index}.selected`,
            reload: true,
        });
    }

    /** Bound to t-on-change, not t-on-input: one reload when the box is left or Enter is pressed. */
    async setTextFilter(optionKey, ev) {
        const value = ev.target.value.trim();

        if (this.controller.cachedFilterOptions[optionKey] === value) {
            return;
        }

        await this.filterClicked({ optionKey, optionValue: value, reload: true });
    }

    async setModeFilter(optionKey, textKey, mode) {
        if (this.controller.cachedFilterOptions[optionKey] === mode) {
            return;
        }

        // Flipping contains <-> does not contain only changes the result when
        // there is something to match, so skip the round trip on an empty box.
        const reload = Boolean((this.controller.cachedFilterOptions[textKey] || "").trim());

        await this.filterClicked({ optionKey, optionValue: mode, reload });
    }

    /**
     * Clearing touches several keys at once. filterClicked would reload after
     * each one, so write them straight to the (reactive) cached options and
     * reload once - which is exactly what filterClicked does internally.
     */
    async clearServiceTypeFilter() {
        for (const serviceType of this.serviceTypes) {
            serviceType.selected = false;
        }

        this.controller.cachedFilterOptions.laundry_service_type_text = "";

        await this.applyFilters("laundry_service_types");
    }

    async clearAddressFilter() {
        this.controller.cachedFilterOptions.laundry_address_text = "";

        await this.applyFilters("laundry_address_text");
    }
}

AccountReport.registerCustomComponent(LaundryAgedReceivableFilters);
