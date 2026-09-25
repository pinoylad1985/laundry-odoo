/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ReceiptHeader } from "@point_of_sale/app/screens/receipt_screen/receipt/receipt_header/receipt_header";

// The drop-off's ref is what ties the printed ticket to the bag that came out
// of the locker, so it prints beside the service type that explains it.
patch(ReceiptHeader.prototype, {
    get laundryLockerRef() {
        return this.props.order?.laundry_locker_ref || "";
    },
});
