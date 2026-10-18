# Laundry POS — Claude Code Project Context

## What This Project Is
A custom Odoo 19 Enterprise module (`laundry_pos`) for **laundryx** that adds a laundry-specific
workflow to the Point of Sale (POS). It is self-hosted on a single VPS via **Docker Compose** (production + staging — see below).

> **Full feature/technical reference:** [docs/laundry-pos-customizations.html](docs/laundry-pos-customizations.html)
> documents every customization in detail. End-user guide: [docs/laundry-pos-module-guide.html](docs/laundry-pos-module-guide.html).
> Keep those in sync when behavior changes.

## Deployment
Self-hosted on a single VPS via **Docker Compose** (Odoo 19 + Postgres + Caddy reverse proxy with HTTPS),
fronted by Cloudflare.
- **Production** — `main` branch → `odoo.laundryx.app` (database `laundryx`).
- **Staging** — `staging` branch → `staging.laundryx.app` (database `laundryx_staging`).

Promotion flow: work → push to `staging` → run the staging deploy → test → open a PR to merge `staging` → `main`
→ run the production deploy (backs up first, pulls `main`, restarts, upgrades the module).

Deploy scripts live on the VPS in `/opt/odoo` and **take the module(s) as an argument** — bare invocation just
prints usage. They run the module upgrade themselves, so **do NOT** follow a deploy with
*Apps → Update Apps List → Upgrade*; that is handled.
```bash
ssh -t root@72.62.244.190 'cd /opt/odoo && ./deploy-staging.sh laundry_pos'   # pauses for a `yes` prompt
```
Deploy only the module(s) actually being promoted — `laundry_pos` and `laundry_account_reports` move separately.

- **GitHub repo:** https://github.com/pinoylad1985/laundry-odoo
- **Odoo version:** 19 (Enterprise) · **Python:** 3.13 · **Frontend:** OWL 2

## Module Structure
```
laundry_pos/
├── __manifest__.py                  # depends: point_of_sale; assets glob static/src/**/*.{scss,js,xml}
├── models/
│   ├── pos_order.py                 # laundry_* fields on pos.order (+ computed due/phone/address + laundry_secondary_type); PosOrderLine.laundry_actual_weight
│   ├── res_partner.py               # pos_tag_names computed char (for receipt tags)
│                                    #   + phone_last10() and the one-customer-per-number guard
│   ├── laundry_service_type.py      # laundry.service.type catalog model (5 seeded types)
│   └── laundry_order_service.py     # laundry.order.service — ONE ROW PER SERVICE per order
│                                    #   (staff / processed date / status) + the WDF mirror;
│                                    #   shown as stacked lines INSIDE the existing 3 columns
├── security/ir.model.access.csv
├── views/pos_order_views.xml        # backend POS Orders list → adds Service Type + Secondary Type columns
├── data/
│   ├── laundry_service_type_data.xml   # seeds the 5 service types
│   └── demangle_server_action.xml      # one-time Contacts maintenance action (not POS runtime)
└── static/src/
    ├── utils/
    │   ├── laundry_instructions.js  # LAUNDRY_MENU: 4 service products matched BY NAME
    │   ├── laundry_products.js      # name matching, TAT helpers, configured-line vals, wdfBilledQty
    │   ├── laundry_storage.js       # localStorage per-order persistence (keyed by uuid)
    │   └── partner_search.js        # multi-word partner match + server-search domain builder
    ├── new_order_modal/             # 4-step modal (customer / services / type / schedule)
    ├── settle_modal/                # Settle modal: customer search → settle orders/invoices/due/deposit
    └── overrides/
        ├── pos_store.js             # addNewOrder flag, selectPartner block, no-merge, printReceipt, getDefaultSearchDetails, pay gates (incl. WDF min-weight billing), addLineToCurrentOrder (WDF qty guard + grid-add weight)
        ├── navbar_patch.*           # New Order / Settle Order / Order List hub buttons (active-highlight + mutual-exclusion + z-index lift; refund locks New Order + Settle)
        ├── product_screen_patch.*   # New Order modal, setup banner, grid lock, rehydrate, _laundryPurpose (sell/settle)
        ├── ticket_screen_patch.*    # Order List customer-search bar (multi-word + server search); full-order refund (no partial) + reworded note
        ├── partner_search_patch.js  # multi-word partner search in the core Customer picker (⚠ upgrade note below)
        ├── partner_block_patch.*    # redirect core settle/deposit menu items into the Settle modal
        ├── order_display_patch.js   # fixed line order (WDF→Press→DWC→Shoe)
        ├── order_summary_patch.js   # tap a laundry line → fresh configurator; WDF billing sweep
        ├── order_line_patch.*       # per-line receipt attributes (incl. WDF Actual Weight), WDF/Press count lines, WDF qty shown to 1 decimal
        ├── pos_order_line_patch.js  # qty rules (DWC/Shoe=1; WDF = weight, LOCKED from manual numpad edits; Press>1) + allowWdfQty guard
        ├── product_configurator_popup_patch.* # Actual Weight input (required) + weight stash, pre-fill, TAT lock, crash guard
        ├── order_receipt_patch.*    # multi-copy thermal receipt
        ├── receipt_header_patch.* + receipt_header.xml + laundry_receipt.scss
        └── opening_control_popup_patch.* / closing_popup_patch.* / cash_control.scss  # cash control
```

> **No `product_template.py` and no product views.** The old "Laundry Services tab" / `laundry_service_type_ids`
> M2M / `productsToDisplay` filtering were **removed**. Products are now recognised by NAME (Wash-Dry-Fold,
> Dry/Wet Clean, Shoe Clean, Press) via `LAUNDRY_MENU`.

## Key Technical Decisions
- **No `_load_pos_data_fields` override on `pos.order`** — its default returns `[]` (read ALL fields), so our
  selection fields load automatically. Overriding it to a specific list would drop lines/partner/amounts.
- **Order fields are computed-stored, NOT `related`** — a stored `related` is writable and would push values back to
  `res.partner` (POS could wipe the customer's phone on sync). Computed = one-way (partner → order).
- **`addNewOrder` stays synchronous** — it's called 10+ places without `await`; async leaves `order.uuid` undefined
  before navigation → `/product/undefined` crash. We set `order._needsLaundrySetup` and let ProductScreen react.
- **Products matched by NAME, not IDs/tags** — survives DB restores and re-seeding; no per-product config needed.
- **localStorage for laundry meta** — the `laundry_*` JS fields aren't server-synced, so they're rehydrated across reloads.
- **Turnaround (TAT) is computed from the schedule and locked** — not a free cashier choice; keeps lines/receipt consistent.
- **Configurator crash guard** — `initAttributes()` pre-seeds `state.attributes` for every value's `attribute_id` so
  malformed products (e.g. after a DB restore) don't crash the popup.
- **Nothing is pre-selected in the configurator on a laundry service** (v1.5.8) — core ticks the FIRST
  value of every attribute, so a size/type nobody chose could ride onto the line and its price just by
  pressing Add. `initAttributes()` clears them back to `[]` (core's own empty shape) for the 5 laundry
  products. **Turnaround is exempt** — the schedule sets it and it renders locked. The gate already
  existed: `confirm()` refuses to Add while `laundryAllAttributesSelected` is false, and the template
  prints "Please select all options before adding." Same reasoning as the New Order date strip.
  ⚠ The deployed Odoo 19 build keys `state.attributes` by **`attribute_id.id`**; the public 19.0 source
  has since moved to `attribute_line_id.id`. Verified against the container 2026-10-16 — re-check on
  upgrade, as every pre-select in that file depends on it.

## Navbar hub buttons (New Order / Settle Order / Order List)
The navbar (`navbar_patch.*`) carries three buttons (desktop = text, mobile = icons). A blank order has no chosen
purpose; clicking decides it. **There is NO setup banner for a blank/idle order** — only the navbar buttons + a locked
product grid ("Tap New Order or Settle Order above to begin").
- **New Order** → opens the New Order setup modal **for the current order** (does NOT create/navigate to a new order —
  that navigation fails under the POS service worker; use the native ＋ to start the next order). Dispatch: navbar fires a
  `laundry-action` DOM event; `ProductScreen._runLaundryAction` handles it.
- **Settle Order** → opens the Settle modal (`settle_modal/`), a customer search where each row surfaces that customer's
  real `pos_settle_due` action (Settle orders / invoices / due / Deposit). The Control Button customer pre-fills it.
- **Order List** → navigates to `TicketScreen`; `ticket_screen_patch.*` adds a customer-search bar above the core SearchBar.
- **Mutual exclusivity via `order._laundryPurpose`** (`'sell'|'settle'`, set on click in `_runLaundryAction`): once set,
  the navbar getters `laundryActiveSell`/`laundryActiveSettle` disable the *other* button and highlight the active one
  (solid primary). Restored after reload from stored status / settle lines. To switch an order's type, start a fresh order.
- **Buttons need `position-relative; z-index`** — the core `pos-leftheader` has an invisible `position-relative w-100`
  layer that (per CSS painting rules) sits over the leftmost button and eats its clicks; the lift fixes it. Don't remove it.
- **Order List is independent of the Customer Control Button** — `PosStore.getDefaultSearchDetails` is overridden to
  always return a blank search; core otherwise seeds the order search with the current order's partner name.

## Service Types (seeded data + frontend list)
| Code | Label |
|------|-------|
| `dropoff` | Drop-off |
| `dropoff_delivery` | Drop-off & Delivery |
| `pickup_delivery` | Pickup & Delivery |
| `locker` | Locker |
| `self_service` | Self-service |

## Receipt tax lines, disclaimer, ORDER # — v1.4.18
- **Disclaimer at the very BOTTOM of every copy** (v1.4.18; it was at the top in 1.4.17): "THIS IS NOT AN
  OFFICIAL TAX INVOICE. FOR LAUNDRY CLAIM TRACKING AND INTERNAL REFERENCE PURPOSES ONLY." — appended with
  `position="inside"` on `.pos-receipt`, so it lands after core's company/contact block and the hidden
  "Powered by Odoo" line. Reuses the `laundry-copy-header` class for the same size/bold + print override.
- **Subtotal / VAT lines only for a VAT Registered fiscal position.** `order_receipt_patch.xml` adds
  `laundryShowTaxBreakdown` to core's `pos-receipt-taxes` `t-if`; the getter matches
  `order.fiscal_position_id.name` **exactly** (trimmed, case-insensitive) against `VAT Registered`. Exact, not
  substring, so "Non-VAT Registered" doesn't match. Renaming that fiscal position hides the breakdown. Total
  always prints.
- **"ORDER # "** is prefixed to the Order Number (`tracking_number`, NOT `pos_reference` which is the Receipt
  Number) inside core's tracking-number wrapper, so it shows only when core shows the number.

## New Order modal keys — v1.4.32 (supersedes 1.4.21–1.4.31)
The modal's choices are laid out as flush blocks of **numpad keys** — core's own
`.numpad-button` (`point_of_sale/.../numpad/numpad.scss`), double height at **88px**, in
`static/src/new_order_modal/new_order_modal.scss` scoped to `.laundry-touch.laundry-neworder`.
- **Colour means ONE thing: selected.** The chosen key is solid `btn-primary`, every other key
  is plain `btn-secondary` grey. The service **+/−** steppers are the only tinted keys left
  (`o_colorlist_item_numpad_color_1` / `_10`) — there red/green IS the control. The v1.4.22
  per-key tints on customer types / service types / dates / hours were removed for this.
  ⚠ A key gets colour ONLY from `o_colorlist_item_numpad_color_N` (it sets `--bg`); with no
  tint class `--bg` is unset and `btn-primary` falls through to solid primary. That's the
  mechanism — don't "fix" it by hard-coding backgrounds.
- **Compound scope `.laundry-touch.laundry-neworder` is deliberate** — the asset glob loads
  `new_order_modal/` before `touch/` (alphabetical), so a plain `.laundry-neworder .btn` would
  lose to `.laundry-touch .btn`. Don't simplify the selector.
- **Service types: 2 columns filled DOWN** (`.laundry-grid-types`, `grid-auto-flow: column` +
  `grid-template-rows: repeat(3, 1fr)`) — left column Drop-off / Drop-off & Delivery /
  Self-service, right column Pickup & Delivery / Locker. The column split is the *order of
  `SERVICE_TYPES`* in `new_order_modal.js`, not markup — reordering that list re-lays the grid.
- **Hours: AM and PM are two separate blocks** with a gutter between them
  (`laundry_pos.HourPills` wraps two `laundry_pos.HourBlock` calls; `amHours`/`pmHours`
  in the JS). Each block is 2 columns of 6 filled DOWN, so a column is a block of time
  (12 AM–5 AM | 6–11 AM · 12 PM–5 PM | 6–11 PM) and 9 AM is never adjacent to 9 PM.
  The halves use `flex: 1 1 0` (`.laundry-hour-half`), NOT Bootstrap's `flex-fill`
  (`1 1 auto`), which would size them by their labels instead of evenly.
- **A date is picked on ONE full-width key: the horizontal date strip** (`DateWheel`,
  `new_order_modal/date_wheel.{js,xml}`). It replaced the 📅 `input[type=date]` in v1.4.23
  and the separate Today / Tomorrow keys in v1.4.29 — those two are now items in the strip
  like any other, marked `(Today)` / `(Tomorrow)` under the date.
  - **It flicks SIDEWAYS**, spanning the last 5 days through ~2 months out (`startOffset: -5`,
    `days: 67`), so a backdated order and a far booking are both reachable.
  - **Every field OPENS centred on today, but NOTHING is pre-selected** (`_initialIndex`
    falls back to today; the state's dates stay `""`). Opening there saves the flick for the
    common case; not selecting keeps a schedule from being confirmed without being looked at.
    While nothing is picked, no item is dimmed either — there is no selection to make stand
    out, so blurring the whole strip would only cost legibility.
  - **`ITEM_W = 120` in `date_wheel.js` MUST equal `$laundry-wheel-item-w` in the SCSS** — the
    scroll offset is read back as an item index (`round(scrollLeft / ITEM_W)`).
  - **`padding-inline: calc(50% - item-w/2)` on the list** is what lets the first and last
    dates reach the centre band, AND what makes `scrollLeft = index * ITEM_W` centre item
    `index` at any modal width. Change one of the two and the JS index goes wrong.
  - `onSelect` fires only after a **150 ms settle**, so sliding past a date doesn't pick it.
  - ⚠ **A programmatic scroll must not count as choosing.** `_scrollToIndex` mutes selection
    for the scroll it triggers (`this._muted`), and `onPointerDown` unmutes the moment the
    cashier touches the strip. Without this the `onMounted` scroll fires the scroll handler,
    whose settle then selects whatever date the strip opened on — which is exactly the
    "default date" that was supposed to be gone (the v1.4.24–1.4.31 bug; it only appeared
    once past days made the opening index non-zero, so `scrollTo` actually moved something).
  - **The selection is a band on the COLUMN; the key itself stays grey.** The key shows the
    neighbours too, so colouring the whole key highlights dates that were NOT picked (the
    v1.4.26 bug). `.laundry-wheel-item-active` = primary + white + bold;
    `.laundry-wheel-item-idle` = grey + `blur(0.7px)`.
  - **`onWillUpdateProps` rolls the strip to a date set from outside** (an order being edited)
    — otherwise the highlighted column would sit off-screen and the key would look empty.
  - The list is `position: absolute; inset: 0`, NOT `height: 100%`: the key's height comes from
    `min-height`, which a percentage height can't resolve against.
- All four date rows are ONE `laundry_pos.DateRow` sub-template (same `t-set` pattern as
  `HourPills`), so the strip exists once instead of four times.
- **Continue is ALWAYS enabled; pressing it when incomplete marks the gaps red.** It used to
  be `disabled` until `canConfirm`, which said nothing about *why*. Now `confirm()` sets
  `state.showErrors` and returns; every `missing*` getter (and `isMissing(stateKey)`, which the
  date/hour sub-templates call with the `field` they were handed) is gated on that flag, so the
  modal never scolds a cashier about a section they haven't reached. The marks clear themselves
  as each gap is filled. `.laundry-invalid` is an **outline**, not a border — the key grids are
  flush blocks, so a border would resize every key inside them.
- `fmtDateLabel` splits `YYYY-MM-DD` and builds a **local** `new Date(y, m-1, d)`. Do NOT pass
  the string to `new Date()` — a bare date string is parsed as UTC and renders as the previous
  day here.

## Touch sizing for our modals — v1.4.20
The POS runs on touchscreen laptops. Our dialogs were built at mouse metrics (lots of `.btn-sm`,
~31px, spaced `gap-1` = 4px); they are now held to a **44px tap-target floor with 8px between
adjacent targets**.
- **One stylesheet, `static/src/touch/laundry_touch.scss`, scoped to `.laundry-touch`** — passed by
  each of our dialogs as the Dialog **`contentClass`** prop. That prop lands the class on
  `.modal-content`, which is why it also covers the **footer** buttons; `bodyClass` would not, since
  the footer is a slot rendered outside the body div. Core POS dialogs keep their own metrics.
- `.btn` becomes `inline-flex` + centred, because `min-height` alone leaves the label pinned to the
  top of the button. `.btn.text-start` re-left-aligns the list-style buttons (service types, refund
  tabs).
- Template-side, `.btn-sm` was dropped and `gap-1` widened to `gap-2` on: the 24 hour pills, the
  quick-date row (both since rebuilt as numpad grids — see v1.4.23), the service +/− steppers, the customer-row Unselect / Edit Details, and every
  Settle row action (those take payments — they were the smallest, tightest targets in the module).
- **RefundGatePopup's three mode tabs left the `btn-group`** (flush, zero gap) for a stacked
  `gap-2` column, so two different approval paths are never a near-miss apart.
- `ReprintCopiesPopup` already followed this (whole row is the target, `p-3`) — it's the pattern.
- ⚠ The **product configurator** is CORE's dialog, so it has no `.laundry-touch`; its pills are
  already `btn-lg` and our weight/note inputs are `form-control-lg`.

## Express pricing per item — v1.4.19
Express used to be a flat **+50** (the `price_extra` on the Turnaround "Express" value). It is now a
**per-item price**: every attribute value carries **`laundry_express_price`** ("Express Price",
`models/product_attribute.py`), edited right next to Extra Price on the product's Attributes tab
(views inherit `product.product_template_attribute_value_view_tree` / `_view_form`).
- **Express Price REPLACES Extra Price, it is not added to it.** `0`/unset = charge the normal Extra
  Price even on an express order, so every non-laundry product and any item without an express price
  behaves exactly like core. The Turnaround Express value's own `price_extra` is set to **0** in Odoo.
- **"Is this express?" is read off the SELECTION**, not the order — `laundryIsExpressSelection(values)`
  looks for a selected value whose attribute name starts with `Turnaround` and whose own name contains
  `express` (same convention as `withTatTurnaround` / `isTurnaround`). `laundryEffectiveExtra(value,
  isExpress)` then returns the price one value contributes. Both in `utils/laundry_products.js`.
- **Three paths all had to be covered**, and they price independently:
  1. cart-tap re-configure → `buildConfiguredLineVals` (sums `laundryEffectiveExtra`);
  2. product-grid add → core's `computePayload().price_extra`, which reads the configurator's
     `get priceExtra()` — **overridden** in `product_configurator_popup_patch.js`;
  3. the modal's header total — same overridden getter (core builds the title from it).
  A schedule change re-prices existing lines for free: `_reapplyTatToLaundryLines` removes and re-adds
  each line through `buildConfiguredLineVals`.
- The **price pill** next to each option shows the effective price via `laundryValueExtra(value)`,
  patched onto `BaseProductAttribute.prototype` (both `RadioProductAttribute` and
  `PillsProductAttribute` templates), so the pill never disagrees with what gets billed.
- ⚠ `_load_pos_data_fields` on `product.template.attribute.value` is **extended with super()**, not
  replaced — dropping core's list would break the configurator entirely.

## Reprint copy picker — v1.4.16
Reprinting used to always print the FULL set of copies. Now `PosStore.printReceipt` opens
**`ReprintCopiesPopup`** (`static/src/reprint_picker/`) so the cashier picks which copies to print.
- **First print vs reprint:** the print straight after payment prints everything automatically (no extra tap
  at checkout); every print after that opens the picker. The ONLY reprint signal is **"this order has already
  been printed"** — `order._laundryPrinted` (in-memory) or `lsWasPrinted(uuid)` (localStorage, key
  `laundry_pos_printed`), set together at the end of `printReceipt`. Persisting it means a reload, or a later
  session's Order List reprint, still gets the picker.
- ⚠ **Do NOT use `opts.order` as a reprint signal.** That was the first attempt and it was wrong: core passes
  the order on the **post-payment** print too, so the picker appeared on the very first print.
- **Nothing is pre-ticked** and Print is disabled until at least one copy is selected, so a stray tap
  prints nothing. Cancel / empty selection prints nothing and returns early.
- **Copy order = SHOP → TRANSACTION (1/n…n/n) → CUSTOMER**, set once in `computeLaundryCopies` and used for
  BOTH the picker's row order AND the physical print sequence (the automatic post-payment print included).
  Shop copy leads because it's the one most often reprinted on its own.
- **`laundryReprintOptions(order)`** (in `overrides/order_receipt_patch.js`) = `computeLaundryCopies` plus a
  **disabled** CUSTOMER COPY row when the service type doesn't produce one (Pickup & Delivery, Locker) —
  greyed with a reason rather than hidden, so its absence is visible and not confusing.
- The tick is a **FontAwesome icon, not a native `<input type=checkbox>`** — OWL's `t-att-checked` sets the
  attribute, which desyncs from the DOM property once the browser toggles it natively. Don't "simplify" it back.

## Schedule display (Pickup / Delivery / Claim) — v1.4.15
The cashier's picked schedule lives on `order.laundry_schedule`, a **plain in-memory JS prop** — it does NOT
survive a page reload, an order re-sync, or a different device/browser. `laundry_service_type` (a real stored
field) always does. So **never gate a schedule fallback on `laundry_service_type` being missing** — that was
the v1.4.14 bug: both fallbacks (the localStorage rehydrate in `product_screen_patch`, and the receipt's own)
were `if (!svcType)`, which is never true for a set-up order → the Pickup/Delivery lines silently vanished
from the receipt + setup banner after any reload, while Service Type / TAT / Customer Type still printed.
- **The durable source is the stored fields** — `laundry_pickup_datetime` / `laundry_delivery_datetime` /
  `laundry_claim_datetime` on `pos.order`, written at modal submit. `fmtStoredDateTime()` in
  `utils/laundry_products.js` renders them in the same `YYYY-MM-DD h:mm AM` shape as `fmtDateTime12()`.
- **Resolution order** (receipt `receipt_header_patch` + banner `product_screen_patch`): in-memory
  `laundry_schedule` → localStorage → **stored datetime fields**. The last one always works.
- POS loads datetime fields as **luxon DateTime in local time**; `fmtStoredDateTime` also handles a raw
  string/Date defensively (serialized values are UTC and must be shifted to local, else PH time is off by 8h).

## Customer Account payment gate — v1.4.14
On a **Drop-off**, **Drop-off & Delivery**, or **Self-service** order, tendering via **Customer Account**
(the pay-later / on-account method, `pos.payment.method.type == 'pay_later'`) requires a **manager PIN**. The
remaining service types (**Pickup & Delivery**, **Locker**) are unaffected — Customer Account is allowed freely.
- The gated set lives in ONE place — `ACCOUNT_GATED_SERVICE_TYPES` in `overrides/payment_screen_patch.js` —
  used for both the gate check and the popup message (`No Customer Account for the following service types: …`).
- Gated in `addNewPaymentLine`: if the tapped method is `pay_later`, the order's `laundry_service_type` is in
  that set, and it isn't approved yet, the add is blocked and the **`ManagerPinPopup`** (`static/src/manager_gate/`)
  opens. On a valid PIN (`pos.order.check_laundry_manager`, the same `is_laundry_manager` check the refund gate
  uses) it records the manager and re-enters to add the line.
- **Approval is per-order and sticky:** once approved, `pos.order.laundry_account_approved_by` (a stored Char,
  set frontend-side, synced like the other laundry fields; an optional column on the POS Orders list) holds the
  manager's name and the tender can be added/removed/re-amounted without re-prompting.
- ⚠ **Operational dependency:** if NO employee has `is_laundry_manager` + a PIN, this gate can never be
  satisfied → Customer Account is effectively **blocked** on Drop-off orders. Configure managers first.

## Service Products (matched by name)
| Code | Name contains | Quantity behavior |
|------|---------------|-------------------|
| `wdf` | Wash-Dry-Fold | weight-based — **Actual Weight** entered in the configurator (required, 2 decimals); billed qty = `max(actual rounded to a WHOLE kg, min)` — decimal **above 0.40 rounds up**, 0.40 and below rounds down; min = 6kg single / 4kg each multi. Qty **locked from manual numpad edits**. See below. |
| `dwc` | Dry/Wet Clean | locked to 1; long turnaround |
| `shoe` | Shoe Clean | locked to 1; long turnaround |
| `cap` | Cap Clean | locked to 1; long turnaround |
| `press` | Press | may exceed 1 |

## Wash-Dry-Fold weight & billing
WDF is weight-based. The **Actual Weight (KG)** is entered in the product configurator (a custom input we
added; **required** — Add is blocked without it) and stored on **`pos.order.line.laundry_actual_weight`** (a
real, loaded field) so it survives reloads/reprints. It's shown as-is, like a variant attribute
("Actual Weight (KG): 3.2"), in the cart and on the receipt; the line **qty** displays to 1 decimal.
- **Billed qty = `wdfBilledQty(actual, count)` = `max(wdfRoundedKg(actual), minKg)`**. `wdfRoundedKg` rounds
  to a **WHOLE kg** — a decimal part **above 0.40 rounds UP**, 0.40 and below rounds DOWN (6.40→6, 6.41→7).
  minKg = **6** (single WDF line) / **4** (2+ lines). Helpers in `utils/laundry_products.js`, used below.
- **Applied at configure (Add)** — `OrderSummary._laundryApplyWdfBilling` sweeps all configured WDF lines for
  the current count — **and re-checked at payment** — `PosStore.pay` shows a **"Click here"** dialog that re-bills
  every WDF line (bumps short lines UP, and a previously force-bumped line back DOWN when the count/min changes).
  "Click here" does NOT auto-proceed; the cashier reviews and presses Pay again.
- **Manual qty edits are blocked for WDF** (numpad/typing) via a guard flag in `pos_order_line_patch.js`; only the
  configurator and the min-weight bump set qty (wrapped in `allowWdfQty(...)`; `PosStore.addLineToCurrentOrder`
  wraps line creation in it too). Setting 0 / removing the line still works.
- **Grid-add path:** adding a WDF from the product grid runs Odoo's *core* auto-configurator, which drops our
  custom payload — so the configurator stashes the weight on confirm (`consumeWdfWeight`) and
  `PosStore.addLineToCurrentOrder` applies it once the line exists. (The cart-tap path uses our own
  `_laundryConfigureLine`.)
- ⚠ **Three places REBUILD a laundry line (remove + re-add), and each must carry the line's data
  across by hand** — `buildConfiguredLineVals` returns product + attributes + price_extra and
  nothing else, so `qty`, `laundry_actual_weight` and `customer_note` are copied explicitly:
  `_laundryConfigureLine` (cart tap) and **`_reapplyTatToLaundryLines`** (a schedule change in the
  New Order modal). The latter copied only `qty` until v1.4.33 — changing the schedule after a WDF
  was configured wiped its Actual Weight, which silently dropped the weight line AND the rounding
  note from the receipt (both are gated on it in `order_line_patch.xml`) and let the pay-time sweep
  re-bill the line at the bare 6KG minimum. Anything added to a laundry line from here on has to be
  added to BOTH rebuilds.

## Per-service progress (staff / processed date / status) — v1.5.6
An order may carry several services at once, each handled by a different person and finished at a
different time, so the three progress values live on **`laundry.order.service`** — one row per
service per order (`name`, `staff_id`, `processed_datetime`, `status`), not on the order.
- **The rows are DERIVED from `laundry_services`**, the comma-joined list of service labels the POS
  already writes at modal submit. `pos.order._sync_laundry_services()` runs from `create` and from
  `write` (only when `laundry_services` is in the vals) and adds/retires rows. **A row that someone
  already signed for is never retired** — only untouched rows are dropped when a service leaves the
  order, so work done stays on the record as history.
- **Status is computed, never typed** — `staff_id` set = In Process, `staff_id` + `processed_datetime`
  = Done. This is the rule the old *"Orders Status Update"* automation applied to the order;
  that automation was **deleted** in v1.5.0 (removing the record from `data/automations.xml` deletes
  it on upgrade). Don't re-add it — it would fight the WDF mirror. The Firebase webhook in that file
  is untouched and stays enabled.
- **"Pending" now means `laundry_all_done = False`** — a stored roll-up on `pos.order`, True only when
  **every** service is Done. It replaced `laundry_status in ('Not Started','In Process')` in **three**
  places that must stay in step: the Orders Pending action (`views/pos_order_pending_views.xml`), the
  `PENDINGS` `ir.filters` (`data/pos_order_filters.xml`) and `_compute_laundry_due_icon` (the urgency
  icon clears only when all services are done).
  - **With no service rows it falls back to the order's own status — and asks whether that status is
    still OPEN**, `laundry_status not in LAUNDRY_OPEN_STATUSES` (`'Not Started'`, `'In Process'`;
    defined in `models/pos_order.py` and mirrored in `laundry_pos/__init__.py` for the SQL paths).
    Some fallback is needed or every order predating `laundry_services` floods Orders Pending.
  - ⚠ **Ask "is it open?", NEVER "is it finished?".** v1.5.0–1.5.2 asked `laundry_status == 'Folded'`,
    which put **161 finished orders from 2025-10-12…16 back into Orders Pending** — their status was
    the legacy `'Done'`, which is neither `'Folded'` nor an open status. Phrased as "is it open?", an
    unknown or blank status can never hold an order in the list.
  - ⚠ `data/pos_order_filters.xml` is `noupdate="1"`, so its domain does **not** reload on upgrade —
    the 1.5.0 migration rewrites that filter's domain in SQL. Any future change to the pending rule
    needs the same treatment.
- **NO new column, and one order stays ONE row.** The list keeps the three columns it always had —
  **Status / Staff / Processed Date** — and each *cell* now stacks **one line per service**
  (`static/src/backend/service_lines_field.*`: `laundry_service_status` / `_staff` / `_processed`,
  three subclasses of one component differing only in `static part`).
  - **The three cells stack in the same order** (and the same order as the Services column), with a
    fixed `min-height`/`line-height` per line, so a row reads **across** as well as down. An unworked
    value is an em-dash, never blank — a blank would slide the columns out of step.
  - **Each line is a tap target** that opens the PIN wizard on THAT service.
  - ⚠ v1.5.0 shipped this as a 4th "Services Progress" column instead; the owner's requirement is
    *the existing* columns, one row per order. Don't re-add a combined column.
- **All three widgets read ONE field, `laundry_service_block`** — a JSON string carried in the list
  arch as `column_invisible="1"` (a hidden carrier, not a column; its label is
  "Service Progress (internal)" so a stray Studio placement is self-explaining).
  - **JSON, not the One2many.** A list cell widget only receives the fields declared in the arch, and
    an x2many's sub-fields can't be declared inside a cell. Same trick as the `laundry_services_list`
    column; JSON rather than a delimiter because staff and service names are free text that would
    collide with any readable separator. Non-stored, or it would bump `write_date` on every order
    whenever a service moved.
  - The datetime is sent as stored **naive UTC** and converted in the widget (`deserializeDateTime`);
    formatting it server-side would bake the server's timezone into the cell.
  - **No service rows → the cell falls back to the order's own value** (the field the widget is
    attached to), so orders predating this still show their single staff/date/status, and tapping
    opens the wizard's order-level branch.
- **The three order-level fields keep their labels (Staff / Processed Date / Status) but hold
  WASH-DRY-FOLD values, read-only.** They always described WDF (the staff PIN dialog is titled
  "Wash-Dry-Fold Staff"); they are now a **one-way mirror** of the order's Wash-Dry-Fold row
  (`laundry_order_service._mirror_wdf_to_order`). The columns named after them show *every* service.
  - **One-way on purpose** — two-way would make two places able to disagree about one fact. Record
    progress by tapping a service line; the mirror follows.
  - **Kept stored, NOT computed** — a computed field would recompute over the whole order history on
    upgrade and wipe the legacy values. `laundry_account_reports` also reads `laundry_staff_id` as the
    cashier.
  - `laundry_status`'s stored value stays **`'Folded'`**, just relabelled **"Done"** (an order now
    holds services that are never folded). `laundry.order.service.status` uses `'Done'` outright —
    new table, no history to migrate. The mirror maps between them.
- ⚠ **A readonly `<field>` on a Selection crashes the whole view on a value that is no longer one of
  the options** — core reads the label as `options.find(...)[1]`, so an unknown value is
  `Cannot read properties of undefined (reading '1')` and the form/list dies (v1.5.0 bug, hit by
  clicking through to the order form). Our selections are read over years of Studio-era history, so
  the order form and the per-service list use **`laundry_selection_text`**
  (`static/src/backend/selection_text_field.js`), which falls back to showing the stored value. Use
  it for any readonly selection we add; the `_status` cell widget does the same lookup inline.
- **`laundry.staff.pin.wizard` now targets a SERVICE** (`service_id`), with the old `order_id` branch
  kept for `action_laundry_set_staff` on orders with no rows. ⚠ `service_id` must **not** be
  `readonly` — the web client drops readonly fields from the save payload, so a context default would
  arrive back empty; it is carried `invisible="1"` and displayed through the related `service_name`.
- **The order FORM records progress too, service by service** (v1.5.5). The inline Services list on
  `pos.order`'s form carries a **`Set Staff / Date` button per row** →
  `laundry.order.service.action_laundry_set_staff()`, which opens the same PIN wizard the list cells
  open, pre-filled from that row. The row's *values* stay read-only: typed straight in, they would
  skip the PIN, and the PIN is the signature on who did the work.
  - An order with **no** service rows shows a single **`Set Staff / Processed Date`** button instead
    (`invisible="laundry_service_ids"`), wired to the order-level `pos.order.action_laundry_set_staff`
    — the legacy branch, which until now had no button anywhere in the UI.
  - **The table sits directly under Due Date, above `All Services Done`** — the services are the
    progress; the roll-up only summarises them, so it is read after.
  - **The three order-level fields are HIDDEN on the form when the order has service rows**
    (`invisible="laundry_service_ids"`, v1.5.6). On such an order they repeat the table's
    Wash-Dry-Fold line word for word, and one fact printed twice looks like it could disagree with
    itself. They are untouched everywhere else — still mirrored, still the Orders-list columns, still
    read by `laundry_account_reports`. The table itself is hidden when there are no rows
    (`invisible="not laundry_service_ids"`), where it would be a header over nothing.
- ⚠ **`laundry_status` may hold a LABEL instead of a stored value, from Studio-era data.**
  `'Done'` is the *label* of `'Folded'`, and 161 orders (2025-10-12…16) stored the label — the 1.2.3
  copy of `x_studio_status` took it verbatim. It broke two things that both ask "is this a value I
  know?": Orders Pending (above) and the readonly Selection widget (`options.find(...)[1]` →
  `Cannot read properties of undefined (reading '1')`, which took the order form down). Fixed by
  **rewriting the data**: `_laundry_normalize_status(cr)` + `LAUNDRY_STATUS_FIXUPS` in
  `laundry_pos/__init__.py`, run from the `19.0.1.5.3` migration **and** from `_laundry_post_init`
  (a staging refresh copies `x_studio_status` verbatim and would reintroduce it). Any further legacy
  spelling goes in that dict. `_laundry_recompute_all_done(cr)` then redoes the roll-up in SQL by the
  same rule as the compute — it is shared with the backfill, so there is one copy of that rule in SQL.
- **Backfill** — `_laundry_backfill_services(cr)` in `laundry_pos/__init__.py`, called from BOTH the
  `19.0.1.5.0` migration (upgrade) and `_laundry_post_init` (install onto a database that already has
  orders, e.g. a staging refresh). It copies the order's single staff/date/status onto **every**
  service of the order — the owner's call: filling only the first service would make every finished
  multi-service order in the history look half-done and reopen it as pending.
- `staff_pin_field.*` is now **unreferenced** (the cells are read-only tap targets and the wizard
  uses a plain many2one + server-side PIN check). Left registered as a harmless fallback; delete it
  if those columns are never made editable again. `folding_pin_field.js` gained a PIN-less
  **`laundry_folding_roller`** variant for the wizard, where the PIN is taken once at Confirm.

## Employee PINs are never readable from this module — v1.5.2
The PIN is the signature on who did the work, so being able to READ one is the same as being able to
sign as that person. Nothing in `laundry_pos` / `laundry_locker` may display, return or route to a PIN.
- **Every PIN input is masked** — `password="True"` on the wizard field, `type="password"` in
  `laundry_pin_dialog`, `manager_pin_popup`, `refund_gate_popup` (both PIN inputs) and
  `rider_signoff_popup`.
- **PINs are compared SERVER-side and never travel to the browser** — `check_laundry_staff`,
  `check_laundry_rider`, `check_laundry_manager`, `check_laundry_rebook` and `get_laundry_staff` /
  `get_laundry_riders` return only `{"id", "name"}`. Don't ever add `pin` to one of those payloads.
- ⚠ **A Many2one to `hr.employee` is a PIN leak unless it is pinned to our own list view.** Past a
  handful of employees the dropdown offers **"Search more…"**, and that dialog opens the model's
  DEFAULT list view — whatever columns anyone (Studio, in this database) has since put on it, PIN
  included. So the wizard's `staff_id` carries
  `context="{'list_view_ref': 'laundry_pos.view_employee_list_laundry_picker'}"` →
  `views/hr_employee_views.xml`, a name / department / job-title list at **`priority` 99** so it can
  never become the model default and nothing can be added to it from Studio. Any new employee picker
  needs the same context.
- **`no_open` on every employee Many2one, readonly ones included** — a readonly M2O still renders as a
  link into the employee form, and the PIN field lives there.
- Core's `hr.employee.pin` is restricted to `hr.group_hr_user`, so a POS-only user can't read it
  anyway — this is about the owner/admin accounts, which can.

## Secondary Type (reporting classification)
`pos.order.laundry_secondary_type` (computed, **stored**) classifies each order **Order / Payment / Adjustment /
Refund** — a column on the backend POS Orders list (`views/pos_order_views.xml`).
- **Refund** wins: `is_refund` OR refunded. Depends on **`lines.refund_orderline_ids`** (NOT the non-stored
  `refund_orders_count`) so a refunded order actually recomputes to Refund.
- **Payment**: settles an order/invoice/deposit, or legacy `x_studio_category == 'Payment'`.
- **Adjustment**: legacy `x_studio_category == 'Adjustment'`.
- **Order**: has a service type (the 5 codes, or a legacy `x_studio_category` service label).
- **else blank** (truly-uncategorized legacy orders stay blank). Legacy data is read from the Studio field
  `x_studio_category`, guarded with `in self._fields` and intentionally **not** in `@api.depends`.
- The old list-view `laundry_manual_category` dropdown was **removed** (superseded by this).

## Refund behavior
- **Refund = whole order.** Clicking Refund (TicketScreen) refunds **every line at full remaining qty** — no
  per-line selection or qty entry. `_setToRefundDetail` snaps to full qty (⚠ full override — re-check on upgrade);
  `onDoRefund` auto-selects all lines. Note reads "Only full refund is allowed. Click Refund to proceed."
- **Refund control gate (v1.4.0, 3 paths @ v1.4.4):** clicking Refund opens `RefundGatePopup`
  (`static/src/refund_gate/`), gated in `ticket_screen_patch.onDoRefund` (blocks the refund unless approved). A
  **typed reason is required on ALL three paths**; pick one tab:
  1. **Rebooked order (same customer)** (`mode:'rebook'`) — reason + rebooked order #, validated by
     `check_laundry_rebook(orig, tn, same_customer=True)` (same `tracking_number` + same customer + later
     `date_order`; blocks on 0/ambiguous matches, since `tracking_number` is **NOT unique**).
  2. **Rebooked order (different customer)** (`mode:'rebook_other'`) — reason + rebooked order # (validated
     with `same_customer=False`: later `date_order`, ANY customer) **+ a manager PIN** (`check_laundry_manager`).
  3. **No rebooking** (`mode:'override'`) — reason + a **manager** PIN only (`hr.employee.is_laundry_manager`).
  Recorded on the refund order: `laundry_refund_rebook_ref` (paths 1–2) / `laundry_refund_manager` (paths 2–3) /
  `laundry_refund_reason` (all) — optional columns on the POS Orders list.
- **Refund payment lock (v1.4.2):** a refund is tendered EXACTLY like the original — same payment method(s) +
  amount(s), negated (partial refund is off, so it mirrors 1:1). `ticket_screen_patch.onDoRefund` stashes the
  original's tenders (`pos.order.get_laundry_refund_payments`) as `_laundryLockedPayments` on the refund order;
  `overrides/payment_screen_patch.js` (on PaymentScreen mount) clears any lines then re-adds the mirrored ones,
  and blocks manual add/delete/amount edits (scoped to locked refund orders only — normal payments untouched).
  ⚠ **Odoo 19 payment API** (verified @19.0 core `payment_screen.js` / `pos_payment.js` — re-verify on upgrade):
  the mirror drives ORDER-level `order.addPaymentline(method)→{status,data}` / `order.removePaymentline(line)` /
  `order.getSelectedPaymentline()` / `line.setAmount(v)` (camelCase — NOT the old `set_amount`); the lock
  overrides the SCREEN-level `addNewPaymentLine` (async) / `deletePaymentLine(uuid)` / `updateSelectedPaymentline`.
- **A refund order locks the hub:** navbar getter `laundryIsRefund` (`is_refund` / has refund lines) disables
  BOTH New Order and Settle Order.

## A number has to BE a number - v1.5.14 (variety rule v1.5.15)
Before anything asks whether a number is a duplicate, it is asked whether it is a phone number at
all: **digits only, and at least 11 of them** (`PHONE_MIN_DIGITS`). One function decides -
`phone_problem()` in `laundry_pos/models/res_partner.py`, which returns a short code
(`'letters'` / `'short'` / `'placeholder'`) or `None`, in that order.
- **11 because that is the length the shop deals in.** A PH mobile is 11 digits (`09171234567`),
  or 12 written with the country code (`+639171234567`), so 11 is the floor that accepts both
  spellings of one number.
  - ⚠ **A Manila landline is 10 digits** (`02 8123 4567`) and is therefore REFUSED. That is the
    trade: the shop reaches customers on mobiles, and the alternative floor (10) would accept a
    mobile with a digit missing. A corporate contact on a landline has to carry it somewhere
    other than the phone field.
- **Letters are refused, not just "unlikely to pass the digit count".** `PHONE_ALLOWED_RE` permits
  digits and the characters people separate them with (`+ ( ) . / -` and spaces) and nothing else,
  so `09171234567 (Globe)` is refused even though it holds 11 digits. The field is read by
  somebody about to dial it.
- **Eleven digits is not enough on its own.** `09111111111` clears the length floor and is still
  somebody leaning on the keypad, so `is_placeholder_phone` (above) also refuses a number built
  from fewer than **4 different digits** - the third of its three shapes, added in v1.5.15. The
  two tests answer different questions and both are needed: the floor catches a number that is
  *incomplete*, the variety test one that is *invented*.
- **A BLANK is still fine.** It is the honest answer when the shop has no number, and the one both
  phone rules deliberately leave open - see the service-type rule below. Every refusal message
  says so, because "that is wrong" is not an instruction and "leave it blank" is.
- **ONE sentence per problem, built server-side** - `res.partner.laundry_phone_problem_message()`.
  It is used by the refusal itself (`_laundry_check_phone`, renamed from
  `_laundry_check_phone_duplicate`), by `laundry_phone_twin_info` (which now answers
  `{'refused': True, 'message': ...}` where it used to answer `{'placeholder': True}`), and by the
  locker's `pos_check_phone`. Two wordings of one rule read as two rules, and invite a guess about
  which is really in force.
  - **TWO SHORT LINES: what is wrong, then what to do** (v1.5.19) - `<number> is too short - a
    mobile number has 11 digits (09171234567).` / `Enter a valid number.` It was a paragraph until
    then, carrying the reasoning behind the rule and the list of service types that require a
    number; the owner's call is that nobody reads a paragraph at a counter with a customer waiting.
    The reasoning lives here instead, read once by whoever maintains the rule. The dialog that
    carries it is titled **Invalid number**.
  - **It does NOT say "or leave it blank"** (v1.5.20, dropped from 1.5.19's wording). Where a
    number is optional is something the cashier learns from the New Order modal, which asks for one
    on three service types and not on the other two - a refusal is not the place to teach a rule it
    isn't enforcing. ⚠ The risk this accepts is the one that filled the book with `00000000000`: a
    cashier who thinks a number is compulsory invents one. What stands against it now is the modal
    not asking, plus `is_placeholder_phone` refusing the invention.
  - The two lines are joined with a newline and survive because core's `AlertDialog` body is
    `class="text-prewrap"` (`web/.../confirmation_dialog.xml`); a `ValidationError` keeps them too.
- **The locker catches it at the Check button, and on the row itself.** `pos_check_phone` returns
  `problem` and NO match, and `_pos_row` carries `phone_problem` so choosing a row whose feed
  number is unusable opens the correction fields immediately. Otherwise the cashier would call an
  unusable number, answer that it rang, press Confirm, and only then be refused by
  `res.partner.create` - having lost the thing Confirm was going to do. `customer_match` is
  cleared to `null` in both paths, which is what keeps Confirm disabled.
- ⚠ **Two waivers, not one, on `write`** - a save is waved through when the **last 10 digits**
  match the stored number, **or** when the value is re-sent **character for character**. The second
  exists because of this rule: a number it refuses has no last-10 key, so a contact whose phone
  somebody once typed as `N/A` would be locked out of the POS customer form for good - that form
  re-sends `phone` whatever was edited, so every attempt to fix the NAME would be refused over the
  number. The rule is about a number being *entered*; re-sending an untouched one is not entering
  it. `partner_form_controller.js` carries the same pair.
- **Not applied to a number ALREADY in the book.** `PHONE_OPTIONAL_SERVICE_TYPES`' `phoneOk` gate
  still asks only whether the selected partner has a phone, so a Pickup & Delivery order can still
  be taken for a legacy contact whose stored number is 10 digits. Tightening that would block the
  counter on data nobody can fix mid-order; this rule stops new ones arriving.

## One customer per phone number - v1.5.9
A contact cannot be CREATED, and its number cannot be SET, when another contact already holds the
same **last 10 digits** - `phone_last10()` in `laundry_pos/models/res_partner.py`, the same key
`laundry_locker` matches its transactions on (that module now imports it from here, so the two
spellings of the rule cannot drift apart).
- **Enforced in `res.partner.create` AND `write`**, server-side, so one rule covers the POS
  customer picker, the backend form, imports and the locker's "create the customer from this
  transaction" alike. Create alone would be a formality - saving the customer with no number and
  typing it in a second later would walk straight past it. `write` only fires when the number
  itself is being set, and never against the record's own number.
- **Matched on DIGITS, in SQL.** `0917 123 4567` and `+639171234567` are one number and no ORM
  domain can say so, so the query strips both sides with `regexp_replace`. No index: creating a
  customer is rare enough that the scan costs less than another stored column would.
- **Archived partners count**, and the message says when the twin is archived ("restore it to use
  it"). An archived duplicate is invisible to the cashier's search, which is exactly how a second
  record for somebody already in the book gets made.
- **Child contacts are exempt on both sides** (`parent_id IS NULL`) - a delivery address under a
  company legitimately carries its parent's number, and blocking that would stop addresses being
  added at all.
- The twin's name is read with **sudo** for the message only: "it exists but I cannot tell you
  which one" is not something a cashier can act on.
- ⚠ **Two people who share one handset now need two numbers** (spouses, a household). That is the
  trade this rule makes - the alternative is a book that silently splits one person's orders, dues
  and points between two contacts.
- **A FILLER number is refused outright** - `is_placeholder_phone()`, v1.5.11, checked
  **before** the duplicate lookup (and, since v1.5.14, after the shape rule above - both are
  decided by the one `phone_problem()`). Seven production contacts carry `00000000000`, from when the
  register asked every walk-in for a number whether the shop needed one or not.
  - ⚠ **v1.5.10 got this backwards and EXEMPTED fillers from the duplicate check.** That let
    `00000000000` be saved freely - which satisfied the required-phone gate below on a Pickup &
    Delivery or Locker order with digits that ring nowhere, i.e. exactly the thing both rules
    exist to stop. A filler is **worse than a blank**: a blank is honest, and a filler *matches*.
  - Refused before the duplicate check because the duplicate message would otherwise answer a
    filler with a stranger's name ("already used by STEVEN") - true, useless, and nothing a
    cashier can act on. The message instead says to leave it blank and names where a number is
    actually required.
  - **Deliberately narrow - three shapes:** one digit repeated; **fewer than
    `PHONE_MIN_UNIQUE_DIGITS` (4) different digits in it** (v1.5.15 - `09111111111`,
    `09090909090`, `09999900000`; min 6 digits, so three-out-of-four stays ordinary); or a
    straight run up or down (`1234567890`, `0987654321`, min 6 digits, counted **mod 10** so the
    9-to-0 wrap still reads as one step). Judged on the whole string **and** on its last 10
    digits, so a filler can't hide behind a country code (`+63 000 000 0000`).
    - **4, not 5.** A PH mobile opens `09`, so the other nine digits must bring at least two
      more between them - every real number does. 5 would refuse `0909 999 9999`, the kind of
      number somebody pays to own, and a refused real number is a customer turned away.
    - It will NOT catch an invented `09171111112` (five different digits) and is not meant to -
      a rule that guesses starts refusing real numbers. The real defence is that the number is
      no longer asked for where the shop doesn't need it.
  - ⚠ **A save that doesn't CHANGE the number is waved through** - judged on the **last 10
    digits**, the one key this module compares numbers on anywhere. The POS customer form
    re-sends `phone` whatever was edited, so without this the seven existing contacts could
    never be edited again - not even to give them a real number. It compared EVERY digit until
    v1.5.13, which read `0917 123 4567` and `+639171234567` as different numbers - the opposite
    of what the duplicate rule itself says. (Harmless as it happened, since the lookup that then
    ran excludes the record's own id, but two tests on one question must not disagree.) Both
    sides must HAVE a key: a number too short for one gives `''` on either side, and treating
    `''` as a match would wave a change from one short number to another straight past the
    filler check.
- It does **not** clean up the duplicates already in the book; it only stops new ones. The seven
  `00000000000` contacts keep that value until someone blanks it.

## The twin is OFFERED, not just refused - v1.5.12
The one-customer-per-number rule above still refuses a duplicate server-side, and has to - it is
what covers imports, the locker and anything else that writes a contact. But a refusal is the
wrong thing to show a cashier: the number being in the book means there IS a customer to serve,
so both places a number gets typed now **ASK** first and offer that customer.
- **One lookup answers both screens** - `res.partner.laundry_phone_twin_info(phone, exclude_id)`
  (`@api.model`): `False` when the number is free, `{'placeholder': True}` for a filler, else
  `{id, name, lines, archived}`. `lines` is **`_laundry_contact_block()`** - name, phone, address,
  the same three lines in the same order as `laundry_customer_block` on `pos.order`. The refusal
  message prints them too: "already used by MARY" is not something anyone can act on, because the
  book holds several people called MARY and the address is what tells them apart.
- **One dialog asks it** - `static/src/phone_twin/phone_twin_dialog.{js,xml}` plus
  `laundryPhoneTwinChoice({orm, dialog, phone, excludeId, confirmHint, cancelHint})`, which
  returns `{status: 'free'|'placeholder'|'confirm'|'cancel', twin}`. Closing the dialog counts as
  Cancel, the gentler of the two.
- **ONE form controller serves the register AND the back office**, because they are the same form:
  `PosStore.editPartner` opens `point_of_sale.res_partner_action_edit_pos`, whose `view_id` is
  **`base.view_partner_form`** with `target="new"` - the very view `views/res_partner_views.xml`
  hangs `js_class="laundry_partner_form"` on. Hence
  `static/src/phone_twin/partner_form_controller.js`, and hence the folder:
  ⚠ **`phone_twin/` must stay loaded by BOTH bundles** (the POS glob picks it up; the manifest adds
  it to `web.assets_backend` by hand). Moved under `backend/`, the POS bundle's
  `('remove', 'backend/**')` would strip it and the register would open a view whose `js_class`
  resolves to nothing.
- **Hooked on `onWillSaveRecord`** (core: "if it returns false, it will prevent the save"), not on
  the phone field - the question is only worth asking about a number somebody has finished typing,
  and a field-level check would put it to them between a 0 and a 9.
- ⚠ **Confirm's work is DEFERRED out of the hook** (`setTimeout`, v1.5.17). The hook runs inside
  `record.save()`, which core wraps in the model's mutex (`save()` = `mutex.exec(() =>
  this._save())`, and `_save` is what calls the hook - `web/.../relational_model/record.js`).
  `discard()` queues on that same mutex, so awaiting it from the hook waits for a lock the call
  itself is holding: the hook never returns, the save hangs with the form still dirty, and
  **Confirm does nothing at all** (the v1.5.12-1.5.16 bug). The hook now returns `false` and the
  navigation runs after `save()` has unwound. Anything else added here that touches the record
  has to be deferred the same way.
- **Confirm means different things in the two places, so each passes its own `confirmHint`:**
  at the register "use that customer" = put them on the order; in the back office there is no
  order, so it = open their record and discard what was typed (no second prompt - they have just
  been shown who holds the number and answered that it is them).
- ⚠ **The register's answer travels through `PosStore.editPartner`**, which is overridden to return
  the customer named by `pos._laundryPhoneTwinPick`. Confirm stops the save, which leaves core
  reading back the form's own record: **nothing** on a new contact (the choice would be dropped) or,
  on an existing one being re-numbered, **the contact that was being edited** - which the customer
  list would then put on the order in place of the twin. The flag puts the choice back on core's
  path, so the list selects it exactly as if it had been tapped.
- **A save that does not CHANGE the number is waved through**, like the server rule - on the **last
  10 digits** (`laundryPhoneKey`, the browser's spelling of `phone_last10`), and judged by
  re-reading the stored `phone`, not the form's dirty state, because that is the value the rule is
  really about. Without it the seven `00000000000` contacts could never be edited again.
- **An archived twin is OFFERED too, under its own button: `Restore and use`** (v1.5.18). It used
  to get Cancel only, on the reasoning that it was taken out of the book deliberately and restoring
  it belongs to whoever can see why. In the shop that is a dead end - the cashier has the customer
  in front of them and is being sent to find backend access - and the way out of a dead end is a
  SECOND contact for the same person, the exact thing this rule exists to prevent. The owner's call.
  - The button is **named for what it does**, and the hint is its own sentence (`restoreHint`,
    beside `confirmHint`), so a contact never goes back in the book behind a tap labelled Confirm.
  - **Restored BEFORE it is used** - `res.partner.laundry_restore_phone_twin(id)`, called from the
    controller's deferred block. An archived contact is invisible to the customer list, to
    `get_new_partner` and to an ordinary read, so using it first would quietly find nothing.
  - **Not sudo**, unlike the twin's name: restoring is the cashier's own action on their own
    rights, and the record's history should say so. "I cannot tell you who holds it" is useless to
    a cashier, which is why the READ is elevated; that reasoning does not stretch to writing.
  - Still **said rather than hidden** ("That contact is archived."): an archived twin is invisible
    to the cashier's search, which is how a second record for somebody already in the book gets
    made in the first place.
- **A filler gets a plain alert, never a Confirm.** There is no customer behind `00000000000` to put
  on an order, only a number to fix, and answering it with the stranger who holds those zeros would
  be true and useless.
- **The locker picker shows the same three lines** (v1.1.17, `laundry_locker`): `_pos_row()` and
  `pos_check_phone()` carry `partner_lines` (built by `_laundry_contact_block`), and one
  `laundry_locker.ContactBlock` sub-template renders them for BOTH the matched customer and the
  contact a confirmation is about to create. The new contact's third line is the locker site + the
  word LOCKER - not a guess: `_apply_locker_address` gives a contact with no address exactly that,
  and a brand-new contact never has one, so it is shown before it is written rather than found
  afterwards.

## What the CUSTOMER has to carry, BY SERVICE TYPE - v1.5.21 (phone rule v1.5.10)
The New Order modal decides what the contact on an order must hold, and it depends on the
service type. ONE table says so - **`contactRequirements`** in
`new_order_modal/new_order_modal.js`, fed by `PHONE_OPTIONAL_SERVICE_TYPES` and
`TAGS_REQUIRED_SERVICE_TYPES`:

| | Drop-off | Drop-off & Delivery | Pickup & Delivery | Locker | Self-service |
|---|---|---|---|---|---|
| Phone number | - | yes | yes | yes | - |
| Tag | - | - | - | yes | - |
| Address line 1 **and** 2 | yes | yes | yes | yes | yes |

- **NOTHING is required on the contact FORM, in either place it is opened** (the back-office
  Contacts form and the register's customer form are the same view - see the twin rule above).
  A form that refuses a save is what makes a cashier invent a value to get past it, which is
  where the seven `00000000000` contacts came from. A contact may always be saved half
  finished; the **ORDER** is what asks, because the order is what actually needs the values.
  - ⚠ That is enforced in the **database, not in this module**: `phone` was flagged Required on
    `res.partner` (Studio, pre-dating the module), which is why clearing one of the seven filler
    numbers was refused with a pink field and a red triangle and never reached the server. The
    owner untick it in Studio. If a contact ever refuses to save again with no message from our
    rules, look there first - nothing in `laundry_pos` makes a partner field required.
- **An address is required on ALL FIVE types, so every order now needs a selected contact** -
  there is no anonymous walk-in order any more. The address is what a bag is delivered,
  collected and re-booked to, and the line that tells two customers of the same name apart
  everywhere this module prints a contact (`_laundry_contact_block`, the Orders list, the
  duplicate dialog). **Both lines**, because this shop writes the kind of place on the first
  (`RESIDENCE`, `OFFICE`) and the address itself on the second - one without the other is half
  an address. **Skip for now still skips**, so a rush order is still takeable and completed later.
- **A tag on Locker only.** A tag is how this shop classifies a customer and the one
  classification that reaches the receipt (`res.partner.pos_tag_names`). A locker bag is handed
  to a machine rather than to a cashier, so on that service type it is the only thing on the
  order that says what kind of customer left it.
- **The gaps are NAMED, with the contact form's own words** (`Phone number`, `Tag`,
  `Address line 1`, `Address line 2`) - `missingContactLabels` on the selected-customer card,
  and `requiredContactLabels` on a new customer who has no contact yet, which says what the
  picker is about to ask for **before** it opens instead of letting it be found out one refusal
  at a time. Both red only after a failed Continue, like every other gap.
- **A customer created at the REGISTER defaults to Person** - `PosStore.editPartnerContext`
  (`overrides/pos_store.js`) forces `default_is_company: false`. Core passes an empty context,
  so whatever `is_company` defaults to in this database was what the form opened on; a laundry
  customer is a person, and one filed as a company has no first or last name and reads wrongly
  everywhere the module prints a contact.
- **The reasoning:** a number is how the shop gets back in touch, so it is required wherever the
  laundry leaves the counter or the bag is not handed over in person. A Drop-off or Self-service
  customer is standing right there and collects in person. Asking for a number anyway is what
  filled the book with `00000000000` contacts - and a dummy number is **worse than a blank one,
  because a dummy number matches** (see the one-customer-per-number rule above).
- **A required number can only come off a real contact** (`phoneOk`): a number typed into the modal
  and left there would be a number no `res.partner` holds, so the receipt, the order and the
  backend would each have a different idea of it. So the gate asks for a selected partner *with*
  a phone, not for a string.
- **Enforced in `canConfirm`** like every other gap, with `missingPartnerPhone` marking it red only
  after a failed Continue - same pattern as `missingServices` / `isMissing(field)`.
- **`addCustomerDetails()` opens the CORE customer picker on top of the modal**, so a number can be
  added without losing the service type and schedule already keyed in (same nesting the locker
  picker uses). Create and Edit both live in that picker, which is why ONE method serves a new
  customer and a returning one whose record has no number. The partner is read back off the
  **order**, not from the return value - the picker is what sets it there.
  - ⚠ It sets **`order._laundryPartnerPickAllowed`** for the duration, because
    `PosStore.selectPartner` (`overrides/pos_store.js`) otherwise blocks the picker on an order
    with no chosen purpose. Without the flag the button is dead on a *skipped* order being
    completed (`_needsLaundrySetup === false` + no `laundry_service_type` yet).
- The selected-customer card moved OUT of the Returning-only branch so it shows for **either**
  kind - a new customer whose details were just taken has a contact too, and the card is where a
  missing number has to be visible. It carries the **Edit Details** button when the number is
  missing and required; the search-result rows keep their own.
- **Skip for now still skips** - it saves a partial setup by design and is not gated.
- **A faked number is refused on BOTH kinds of order, required or not** - that is the server-side
  `is_placeholder_phone()` rule above, not this one. The two halves have to be read together:
  this rule stops a number being *demanded* where the shop has no use for it, and that one stops
  a demand being *satisfied* with digits that ring nowhere. Either on its own is useless.

## Important Files NOT to Break
- `models/pos_order.py` — do NOT add `_load_pos_data_fields`; keep order fields computed (not related).
- `static/src/overrides/pos_store.js` — `addNewOrder` must stay synchronous.
- `__manifest__.py` — asset glob `laundry_pos/static/src/**/*` picks up all SCSS/JS/XML.

## ⚠️ Re-check on every Odoo upgrade
- `static/src/overrides/partner_search_patch.js` — `getNewPartners` is a **FULL COPY** of core
  `PartnerList.getNewPartners` (not a `super` extension), because the multi-word search domain has to be
  injected mid-method. It will **not** inherit future core changes. On each Odoo upgrade, diff it against the
  new core method and re-sync if core changed.
- `static/src/overrides/ticket_screen_patch.js` — `_setToRefundDetail` is a **FULL replacement** of core's
  method (not a `super` call), to force full-quantity refunds. Diff against core on upgrade.

  (Everything else in the module extends via `super`, so it auto-inherits core changes — these two are the exceptions.)

## Odoo 19 POS Architecture Notes
- Popup pattern: `makeAwaitable` from `@point_of_sale/app/utils/make_awaitable_dialog`
- POS store: `@point_of_sale/app/services/pos_store` → `PosStore`
- Product screen: `@point_of_sale/app/screens/product_screen/product_screen`
- Configurator: `@point_of_sale/app/components/popups/product_configurator_popup/product_configurator_popup`
- Cash control: opening/closing popups under `@point_of_sale/app/components/popups/`
- Asset bundle: `point_of_sale._assets_pos` · Models loaded via `_load_pos_data_fields` + `_load_pos_data_read`
- OWL: `patch(Class.prototype, {...})`, `useEffect(effect, () => [deps])`, `super.method(...arguments)`

## POS UI Vocabulary (shared glossary)
Use these official Odoo component names so we don't get confused.

**Screens** (full "pages", registered in `pos_pages`):
| Name | Meaning |
|------|---------|
| `ProductScreen` | Main order-taking screen (product grid + cart). Cashiers live here. |
| `PaymentScreen` | Taking payment for the current order. |
| `ReceiptScreen` | Receipt shown after payment. |
| `TicketScreen` | The **Orders** screen — search/browse past & open orders. |
| `LoginScreen` | Lock / cashier-login screen (lock→unlock passes through here). |
| `SaverScreen` | Idle screensaver. |

**Navbar** = the whole top strip (`point_of_sale.Navbar`, file `navbar_patch.xml`). Holds: `Register`
button, `orders-button` (Orders), the ＋ new-order, `OrderTabs` (the `73001…` tabs), product-search
`Input`, barcode button, `CashierName` (avatar), the lock, and the ☰ hamburger (`Dropdown` — Cash
In/Out, Close Register…). *Our additions:* the **hub buttons** (New Order / Settle Order / Order List — desktop text,
mobile icons; the chosen one is highlighted solid-primary and the opposite one is disabled — mutual exclusivity).

**Product screen parts:** `products`/product grid (cards) · **category buttons** (the colored pills =
product categories) · `OrderSummary` = the **cart**, made of `Orderline`s · **control buttons**
(bottom-left: Customer, Note…) · `Numpad` + `Actionpad` (number pad + **Pay**) · *our* **setup banner** above the cart
(green = submitted summary with **Change**; amber = skipped New Order with **Complete**; *no* banner for a blank/idle
order) and the **grid-lock overlay** on the product grid until setup is done — both are NOT the navbar.

**Orders screen (TicketScreen) parts:** `SearchBar` = the "Search Orders" bar · *our* **customer search
bar** above it · the **order list** (rows) · the **detail/refund pane** on the right.

**Popups / Modals** (float over a screen): `PartnerList` = the "Choose customer" picker · **Product
Configurator** · **Opening/Closing control** (cash-count popups) · *our* **New Order modal** and **Settle modal**.
(The old **Action hub** was removed — its actions are now the navbar hub buttons.)

Rules of thumb: **"screen"** = a full page · **"navbar"** = top strip (vs the lower **"setup banner"**) ·
**"popup"/"modal"** = floats on top.
