/** @odoo-module **/

// The value IN FORCE on a locker drop-off, read from a _pos_row() payload.
//
// Each of the two facts a drop-off carries about its customer is a PAIR of
// fields: `customer_name` / `phone` are what the customer keyed into the
// PudoPro screen and are never written over, and `customer_name_corrected` /
// `phone_corrected` are the shop's note against them, blank where nothing was
// corrected. So "the number this bag is actually called on" is the correction
// where there is one and the locker's own where there is not.
//
// These two are the browser's copy of `_laundry_phone_in_force` /
// `_laundry_name_in_force` on the model (laundry_locker_transaction.py). The
// two spellings have to stay in step: the register calls the number it reads
// here, and the server creates the contact under the name it reads there.

export function rowPhoneInForce(row) {
    return ((row && (row.phone_corrected || row.phone)) || "").trim();
}

export function rowNameInForce(row) {
    return ((row && (row.customer_name_corrected || row.customer_name)) || "").trim();
}
