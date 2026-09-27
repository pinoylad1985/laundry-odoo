/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ReceiptHeader } from "@point_of_sale/app/screens/receipt_screen/receipt/receipt_header/receipt_header";

// Every locker ref the feed sends starts `LX-`, so the prefix says nothing a
// receipt reader does not already know - it is the same three characters on
// every ticket, taking up room in the largest type on the page. Stripped for
// PRINTING only: the stored ref keeps its prefix, which is what the feed, the
// dashboard and every search in the backend are keyed on.
const REF_PREFIX = /^LX-/i;

patch(ReceiptHeader.prototype, {
    // The drop-off's ref is what ties the printed ticket to the bag that came
    // out of the locker, so it prints beside the service type that explains it.
    get laundryLockerRef() {
        return (this.props.order?.laundry_locker_ref || "").replace(REF_PREFIX, "");
    },

    // The SITE it was dropped off at - THE RISE or AIR. It prints where the
    // door number used to: a door identifies a bag only to someone standing at
    // that locker, and by the time this ticket is read the bag is at the shop
    // and the door has been let to someone else. The site is the half of
    // "where did this come from" that still means something then.
    get laundryLockerSite() {
        return this.props.order?.laundry_locker_location || "";
    },
});
