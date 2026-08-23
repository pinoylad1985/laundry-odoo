import { _t } from "@web/core/l10n/translation";
import { AccountReport } from "@account_reports/components/account_report/account_report";
import { AccountReportFilters } from "@account_reports/components/account_report/filters/filters";

/**
 * Filter bar of the Aged Receivable (Detailed) report.
 *
 * Adds a Service Type filter (tick as many types as you like) and an Address
 * filter (a contains / does not contain text box). Everything it does is write
 * to the report options; the matching itself happens server side, in
 * laundry_aged_receivable.py, so the customer subtotals and the aging buckets
 * stay consistent with the rows shown.
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

        if (!ticked.length) {
            return "";
        }

        return ticked.length === 1 ? ticked[0].name : _t("%s selected", ticked.length);
    }

    get addressSummary() {
        const options = this.controller.cachedFilterOptions;
        const text = (options.laundry_address_text || "").trim();

        if (!text) {
            return "";
        }

        return options.laundry_address_mode === "not_contains" ? _t('not "%s"', text) : `"${text}"`;
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
     * Unticking one by one would reload once per type, so write straight to the
     * (reactive) cached options and reload once - which is exactly what
     * filterClicked does internally.
     */
    async clearServiceTypeFilter() {
        if (!this.serviceTypes.some((serviceType) => serviceType.selected)) {
            return;
        }

        for (const serviceType of this.serviceTypes) {
            serviceType.selected = false;
        }

        await this.applyFilters("laundry_service_types");
    }

    async clearAddressFilter() {
        this.controller.cachedFilterOptions.laundry_address_text = "";

        await this.applyFilters("laundry_address_text");
    }
}

AccountReport.registerCustomComponent(LaundryAgedReceivableFilters);
