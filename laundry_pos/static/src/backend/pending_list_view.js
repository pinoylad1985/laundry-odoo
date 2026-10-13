/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useBus } from "@web/core/utils/hooks";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";

// Service buttons across the top of the Orders Pending list: All · Wash-Dry-Fold ·
// Press · Dry/Wet Clean · Shoe Clean · Cap Clean.
//
// They are not a filter of their own - each button is one of the `laundry_svc_*`
// <filter> entries declared in the search view (views/pos_order_pending_views.xml),
// toggled through the search model. Two things fall out of that for free:
//
//   * MULTI-SELECT. Those filters sit in ONE separator-delimited group, and the
//     search model OR's a group together, so Press + Shoe Clean reads as
//     "Press OR Shoe Clean". Nothing here has to build a domain.
//   * The buttons and the search bar can never disagree. A service switched off
//     by closing its facet un-highlights its button, because `isActive` is read
//     back off the search model rather than kept in local state.
//
// NOTE the prefix is load-bearing: the buttons ARE whatever filters are named
// laundry_svc_*, so adding a service means adding a <filter> and nothing else.
const SVC_PREFIX = "laundry_svc_";

export class PendingListController extends ListController {
    setup() {
        super.setup();
        // The controller already re-renders on the reload a toggle causes; this is
        // insurance for the cases that change the query without one (clearing the
        // facets, loading a favourite), so a button is never left lit.
        useBus(this.env.searchModel, "update", () => this.render(true));
    }

    get laundryServiceFilters() {
        return this.env.searchModel.getSearchItems(
            (item) => item.type === "filter" && (item.name || "").startsWith(SVC_PREFIX)
        );
    }

    // "All" is not a filter - it is the absence of every service filter, which is
    // exactly what the list shows when none is on. So it lights up by elimination.
    get laundryAllActive() {
        return !this.laundryServiceFilters.some((item) => item.isActive);
    }

    toggleLaundryService(item) {
        this.env.searchModel.toggleSearchItem(item.id);
    }

    clearLaundryServices() {
        const items = this.laundryServiceFilters;
        // Guarded: deactivating a group with nothing in it would still cost a
        // reload, and tapping All while All is lit should do nothing at all.
        if (items.length && items.some((item) => item.isActive)) {
            // One call for the whole group, so clearing four services is one
            // search instead of four.
            this.env.searchModel.deactivateGroup(items[0].groupId);
        }
    }
}

registry.category("views").add("laundry_pending_list", {
    ...listView,
    Controller: PendingListController,
    buttonTemplate: "laundry_pos.PendingListButtons",
});
