/** @odoo-module **/

/**
 * What the CUSTOMER on an order has to carry, by service type.
 *
 * ONE table, read by three places that must agree: the New Order modal (which
 * names the gaps), the contact FORM (whose own `required` modifiers are what
 * turn the empty fields pink and refuse the save - see
 * views/res_partner_views.xml), and the product screen, which re-opens the
 * picker after the modal hands it an "edit this customer" answer.
 *
 * Changing what an order demands is changing `needed` here and nothing else.
 *
 * The reasoning per rule:
 * - PHONE is how the shop gets back in touch, so it is asked wherever the
 *   laundry leaves the counter or the bag is not handed over in person. A
 *   Drop-off or Self-service customer is standing right there. Asking anyway is
 *   what filled the book with 00000000000 contacts, and a dummy number is worse
 *   than a blank one because a dummy number matches.
 * - A TAG is the one classification that reaches the receipt (pos_tag_names). A
 *   locker bag is handed to a machine rather than to a cashier, so there it is
 *   the only thing on the order that says what kind of customer left it.
 * - The ADDRESS, on every type, is what a bag is delivered, collected and
 *   re-booked to, and the line that tells two customers of the same name apart
 *   everywhere this module prints a contact. BOTH lines, because this shop
 *   writes the kind of place on the first (RESIDENCE, OFFICE) and the address
 *   itself on the second - one without the other is half an address.
 */

export const PHONE_OPTIONAL_SERVICE_TYPES = new Set(["dropoff", "self_service"]);
export const TAGS_REQUIRED_SERVICE_TYPES = new Set(["locker"]);

const trimmed = (value) => String(value || "").trim();

// Either field will do: the book carries numbers in both and either can be
// called. Trimmed, so a contact holding a space is still treated as blank.
export const partnerPhone = (partner) => trimmed(partner?.phone || partner?.mobile);

// pos_tag_names is a comma-joined string computed server-side, so it is there
// for both pre-loaded and on-demand partners - unlike the category_id relation,
// whose category records aren't all loaded into the register.
export const partnerTagNames = (partner) =>
    trimmed(partner?.pos_tag_names)
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean);

/**
 * `field`   - the res.partner field, as the form knows it.
 * `label`   - the contact form's own word for it, so a cashier reads the same
 *             name in the modal and on the form.
 * `context` - the key the form's `required` modifier reads.
 * `needed`  - is it asked for on this service type?
 * `has`     - does this partner already carry it?
 */
export const CONTACT_REQUIREMENTS = [
    {
        field: "phone",
        label: "Phone number",
        context: "laundry_require_phone",
        needed: (serviceType) =>
            !!serviceType && !PHONE_OPTIONAL_SERVICE_TYPES.has(serviceType),
        has: (partner) => !!partnerPhone(partner),
    },
    {
        field: "category_id",
        label: "Tag",
        context: "laundry_require_tags",
        needed: (serviceType) =>
            !!serviceType && TAGS_REQUIRED_SERVICE_TYPES.has(serviceType),
        has: (partner) => partnerTagNames(partner).length > 0,
    },
    {
        field: "street",
        label: "Address line 1",
        context: "laundry_require_street",
        needed: (serviceType) => !!serviceType,
        has: (partner) => !!trimmed(partner?.street),
    },
    {
        field: "street2",
        label: "Address line 2",
        context: "laundry_require_street2",
        needed: (serviceType) => !!serviceType,
        has: (partner) => !!trimmed(partner?.street2),
    },
];

// Nothing is asked before a service type is chosen: every rule is per-type, so
// until one is picked there is no question to answer.
export function requiredContactFields(serviceType) {
    return CONTACT_REQUIREMENTS.filter((rule) => rule.needed(serviceType));
}

// A partner that isn't there is missing everything the type asks for - which is
// what makes "no customer at all" fail the same gate as an incomplete one.
export function missingContactFields(partner, serviceType) {
    return requiredContactFields(serviceType).filter(
        (rule) => !(partner && rule.has(partner))
    );
}

/**
 * The same table as a CONTEXT for the contact form.
 *
 * The form's `required` modifiers read these keys, so the register's own
 * mechanism marks the fields - pink field, refused save, "Invalid fields"
 * notice - exactly as a field flagged Required in Studio behaves. That is the
 * point: a message of our own would be a second thing to learn, and would not
 * show WHICH box is empty.
 *
 * Carried in the context rather than flagged on the field, because the demand
 * belongs to the ORDER, not to res.partner: with no key set (the back-office
 * Contacts form, the register's own Customer button) the form asks for nothing
 * and a half-finished contact still saves. A form that always refuses is what
 * makes a cashier invent a value to get past it.
 */
export function contactNeedsContext(serviceType) {
    const context = {};
    for (const rule of requiredContactFields(serviceType)) {
        context[rule.context] = true;
    }
    return context;
}
