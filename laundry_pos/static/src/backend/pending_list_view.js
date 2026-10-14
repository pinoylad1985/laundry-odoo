/** @odoo-module **/

import { onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";

// Service buttons across the top of the Orders Pending list: All, Wash-Dry-Fold,
// Press, Dry/Wet Clean, Shoe Clean, Cap Clean - shaped like the stat buttons on a
// contact form (icon, label, number), on a row of their own above the search bar
// (see the `control-panel-laundry-band` slot in pending_list_view.xml).
//
// They are not a filter of their own: each button is one of the `laundry_svc_*`
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
// laundry_svc_*, so adding a service means adding a <filter>, plus an icon below.
const SVC_PREFIX = "laundry_svc_";

// FontAwesome 4.7 is what the backend bundles, and it has no shoe and no cap, so
// two of these are the ACTION rather than the thing: a brush for the shoes, and a
// mortarboard as the nearest cap. The label carries the meaning; the icon is only
// there to make a button findable at a glance.
const SVC_ICONS = {
    wdf: "fa-tint",             // water
    press: "fa-fire",           // heat
    dwc: "fa-flask",            // solvent
    shoe: "fa-paint-brush",     // brushing
    cap: "fa-graduation-cap",
};

export class PendingListController extends ListController {
    // Not just a buttonTemplate: the band is not in the list's button strip, it is
    // in a slot of its own at the top of the control panel, so the controller needs
    // its own template to set that slot.
    static template = "laundry_pos.PendingListView";

    setup() {
        super.setup();
        this.orm = useService("orm");
        // Reactive, so the numbers can arrive after the first paint instead of
        // holding the list up behind six COUNT queries.
        this.laundryCounts = useState({ total: 0, services: {} });

        onWillStart(() => this._loadLaundryCounts());
        // The controller already re-renders on the reload a toggle causes; this is
        // insurance for the cases that change the query without one (clearing the
        // facets, loading a favourite), so a button is never left lit.
        useBus(this.env.searchModel, "update", () => this.render(true));
        // Counts go stale the moment an order on this list is marked Folded, which
        // is the main thing anyone does here - so they are re-read whenever the
        // records are. Setting the state re-renders but does not reload the model,
        // so this cannot feed itself.
        useBus(this.model.bus, "update", () => this._loadLaundryCounts());
    }

    get laundryServiceFilters() {
        return this.env.searchModel.getSearchItems(
            (item) => item.type === "filter" && (item.name || "").startsWith(SVC_PREFIX)
        );
    }

    // Counted against globalDomain - the action's pending condition - and NOT against
    // the live search domain. Deliberate: a button has to say how many pending orders
    // of that service exist, which is a number that holds still. Counting the current
    // domain would zero out every other button the instant one was pressed, so the bar
    // would stop being a view of the work and become a view of the last click.
    async _loadLaundryCounts() {
        const items = this.laundryServiceFilters;
        const counts = await this.orm.call("pos.order", "laundry_service_counts", [
            this.env.searchModel.globalDomain || [],
            items.map((item) => item.description),
        ]);
        this.laundryCounts.total = counts.total;
        this.laundryCounts.services = counts.services;
    }

    laundryCountFor(item) {
        const count = this.laundryCounts.services[item.description];
        return count === undefined ? "-" : count;
    }

    laundryIconFor(item) {
        return SVC_ICONS[(item.name || "").slice(SVC_PREFIX.length)] || "fa-circle-o";
    }

    // "All" is not a filter: it is the absence of every service filter, which is
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
});
