/** @odoo-module **/

// Persistent per-order laundry details, keyed by order UUID.
// Survives POS reload / module re-entry (laundry_* props are JS-only and not
// synced to the server, so they would otherwise vanish on rebuild).
// Store shape: { [uuid]: { status: 'skipped' | 'submitted', ...details } }
const LS_KEY = "laundry_pos_orders";

export function lsSave(uuid, data) {
    if (!uuid) return;
    try {
        const all = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
        all[uuid] = data;
        // Rotate out oldest entries when the store grows large
        const keys = Object.keys(all);
        if (keys.length > 100) delete all[keys[0]];
        localStorage.setItem(LS_KEY, JSON.stringify(all));
    } catch {}
}

export function lsLoad(uuid) {
    if (!uuid) return null;
    try {
        return JSON.parse(localStorage.getItem(LS_KEY) || "{}")[uuid] || null;
    } catch {}
    return null;
}

// Orders whose receipt has already been printed once — the next print is then a
// REPRINT and offers the copy picker (see PosStore.printReceipt).
//
// Kept in its OWN key, not merged into the record above: lsSave overwrites the
// whole per-order entry, so re-running setup on an order would wipe the flag.
// Persisting it (rather than relying on the in-memory `_laundryPrinted` prop)
// means a reload or a next-session Order List reprint still gets the picker.
const LS_PRINTED_KEY = "laundry_pos_printed";
const LS_PRINTED_MAX = 200;

export function lsMarkPrinted(uuid) {
    if (!uuid) return;
    try {
        const list = JSON.parse(localStorage.getItem(LS_PRINTED_KEY) || "[]");
        if (list.includes(uuid)) return;
        list.push(uuid);
        while (list.length > LS_PRINTED_MAX) list.shift();
        localStorage.setItem(LS_PRINTED_KEY, JSON.stringify(list));
    } catch {}
}

export function lsWasPrinted(uuid) {
    if (!uuid) return false;
    try {
        return JSON.parse(localStorage.getItem(LS_PRINTED_KEY) || "[]").includes(uuid);
    } catch {}
    return false;
}

export function lsDelete(uuid) {
    if (!uuid) return;
    try {
        const all = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
        delete all[uuid];
        localStorage.setItem(LS_KEY, JSON.stringify(all));
    } catch {}
}
