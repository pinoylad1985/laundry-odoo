/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { laundryCodeForProduct } from "@laundry_pos/utils/laundry_products";
import { laundryTurfPromoPercent, TURF_PROMO } from "@laundry_locker/utils/turf_promo";

// Core's own setter, which clamps to 0-100. Guarded the way this module
// guards removeOrderline: the deployed build is not always the public 19.0
// source, and a promotion must not take the register down if the method is
// ever renamed - the field behind it is a plain stored percent either way.
function setLineDiscount(line, percent) {
    if (typeof line.setDiscount === "function") {
        line.setDiscount(percent);
    } else {
        line.discount = percent;
    }
}

/**
 * Put every Wash-Dry-Fold line on this order at the promotion's discount, and
 * take the discount back off one that no longer qualifies.
 *
 * It SWEEPS rather than setting the discount once at the moment a line is
 * made, because a laundry line is not edited - it is REBUILT. Three places
 * remove a line and add it back (a cart tap re-configuring it, a schedule
 * change, and the configurator's own Add), and a rebuild starts from product +
 * attributes alone, so anything set on the old line is gone. The same trap
 * lost the WDF Actual Weight in v1.4.33. Re-deriving it from the order every
 * time is the only version of this that cannot fall out of step.
 *
 * ⚠ It clears ONLY a discount equal to the promotion's own percent. A cashier
 * who has hand-typed some other discount on a WDF line meant it, and this
 * sweep is not entitled to an opinion about it.
 */
function laundryApplyTurfPromo(order) {
    if (!order?.lines) {
        return;
    }
    const percent = laundryTurfPromoPercent(order);
    for (const line of order.lines) {
        if (laundryCodeForProduct(line.product_id?.product_tmpl_id) !== "wdf") {
            continue;
        }
        const current = line.discount || 0;
        if (percent) {
            if (current !== percent) {
                setLineDiscount(line, percent);
            }
        } else if (current === TURF_PROMO.percent) {
            setLineDiscount(line, 0);
        }
    }
}

patch(PosStore.prototype, {
    // EVERY path that puts a laundry line on an order comes through here - the
    // product grid, the cart-tap re-configure, and the schedule-change rebuild
    // all call it - so one hook covers all three. The line exists by the time
    // super has returned, and the discount is a percentage, so it does not
    // matter that the WDF billed quantity is settled afterwards.
    async addLineToCurrentOrder() {
        const line = await super.addLineToCurrentOrder(...arguments);
        laundryApplyTurfPromo(this.getOrder());
        return line;
    },

    // The last gate before money, and the one that catches the case the add
    // hook cannot: a cashier who claims TURF, rings up the wash, then presses
    // Change and swaps to a Makati drop-off. Nothing is rebuilt if the
    // turnaround happens not to move, so without this the Makati bag would
    // keep BGC's half-price.
    async pay() {
        laundryApplyTurfPromo(this.getOrder());
        return super.pay(...arguments);
    },
});

export { laundryApplyTurfPromo };
