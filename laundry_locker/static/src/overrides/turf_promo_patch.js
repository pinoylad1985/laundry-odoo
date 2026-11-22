/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { laundryCodeForProduct } from "@laundry_pos/utils/laundry_products";
import { laundryTurfPromoPercent } from "@laundry_locker/utils/turf_promo";

// The figure this sweep last wrote on a line, kept ON the line. In memory
// only, and that is correct rather than a shortcut: a rebuilt line arrives
// with no discount and no mark, which is exactly what it should be, and a
// reloaded order is still a TURF order (the locker code is a stored field),
// so the sweep simply writes the same figure again. The mark is never the
// thing that decides whether the promotion applies - the locker code and the
// date are - it only says who the discount already on the line belongs to.
const PROMO_MARK = "_laundryTurfPromo";

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
 * ⚠ It touches ONLY a discount it put there ITSELF, and it knows which those
 * are because it remembers the figure it wrote (`_laundryTurfPromo` on the
 * line) and compares the line's discount against it. A discount the cashier
 * typed, or one the Discount button put on every line at once, is somebody's
 * decision about this sale and the sweep has no opinion about it - not when
 * the order qualifies, and not when it stops qualifying either. Judging it by
 * the FIGURE alone is what broke it: a 50% the cashier applied by hand looks
 * exactly like the promotion's own 50%, so pressing Pay on an ordinary order
 * wiped their discount off the Wash-Dry-Fold line and left it on every other
 * line, which is the worst of both.
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
        // Is this discount ours? Only if we wrote it AND nobody has changed it
        // since. The moment a cashier types over it, it stops being ours and
        // we leave it alone from then on.
        const ours = line[PROMO_MARK] != null && current === line[PROMO_MARK];
        if (percent) {
            if (!current || ours) {
                if (current !== percent) {
                    setLineDiscount(line, percent);
                }
                line[PROMO_MARK] = percent;
            }
        } else if (ours) {
            setLineDiscount(line, 0);
            line[PROMO_MARK] = null;
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
