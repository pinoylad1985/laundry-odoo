/** @odoo-module **/

// The TURF launch promotion: half off Wash-Dry-Fold on a bag left at the BGC
// locker, up to and including 31 December 2026.
//
// It is a DISCOUNT on the line, not a lower price, so the receipt prints the
// full price and what the customer saved beside it, and the Odoo reports can
// say afterwards what the promotion cost. A quietly halved price would appear
// nowhere except in the takings.
//
// Keyed on the site's CODE, never on its display name. The name is a label -
// TURF today, AVIDA TOWERS TURF BGC if anyone ever spells it out - and a rule
// keyed on it would stop matching the day somebody retyped it, without
// anything on screen saying so. Same reasoning as LOCATION_AREAS in
// models/laundry_locker_sync.py, which keys the price book on the code too.
//
// ⚠ This is the ONLY place the three facts of the promotion are written. To
// extend it, change `lastDay`; to end it early, change `lastDay` to a date
// already past. Nothing else has to be touched.
export const TURF_PROMO = {
    code: "LX-003",
    percent: 50,
    // INCLUSIVE - an order rung up at any hour of this day still gets it.
    lastDay: "2026-12-31",
};

// The cut-off as a LOCAL instant, built from the parts rather than parsed from
// the string: `new Date("2026-12-31")` is read as UTC and would end the
// promotion at 8 in the morning of the 31st here. Same trap as fmtDateLabel in
// the New Order modal.
function promoEndsAt() {
    const [y, m, d] = TURF_PROMO.lastDay.split("-").map(Number);
    return new Date(y, m - 1, d, 23, 59, 59, 999).getTime();
}

/**
 * The discount percent this order's Wash-Dry-Fold lines are owed - 50 on a
 * TURF drop-off inside the promotion, 0 on anything else.
 *
 * Judged on the clock NOW rather than on the order's own date: the discount is
 * decided as the sale is rung up, and once set it lives on the line as a
 * stored value, so a reprint or a refund months later reads back what was
 * actually charged instead of re-deciding it.
 *
 * @param {object} order
 * @param {number} [now] epoch ms, for tests
 * @returns {number} 0-100
 */
export function laundryTurfPromoPercent(order, now = Date.now()) {
    if (!order || order.laundry_locker_code !== TURF_PROMO.code) {
        return 0;
    }
    return now <= promoEndsAt() ? TURF_PROMO.percent : 0;
}
