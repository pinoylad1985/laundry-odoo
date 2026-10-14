import logging

from . import models

_logger = logging.getLogger(__name__)

# Staff that were renamed: legacy name (in orders' x_studio_staff) -> current employee name.
LAUNDRY_STAFF_ALIASES = {"Jepoy": "Jeff"}
# Resigned employees the module keeps archived (off the Staff picker; history preserved).
LAUNDRY_RESIGNED_EMPLOYEES = ("Lester", "Joey", "Kate", "Lucy", "Lita", "Mona")
# Employees seeded as delivery riders (is_laundry_rider). Still configurable afterward.
LAUNDRY_RIDERS = ("Carl", "Fernan", "Felix", "Jim", "Jeff")
# Legacy x_studio_category service labels -> laundry_service_type codes (backfill).
# Adjustment / Payment / blank are NOT service types and are intentionally left out.
LAUNDRY_SERVICE_TYPE_BY_CATEGORY = {
    "Drop-off": "dropoff",
    "Drop-off & Delivery": "dropoff_delivery",
    "Pickup & Delivery": "pickup_delivery",
    "Locker": "locker",
    "Self-service": "self_service",
}


def _laundry_backfill_services(cr):
    """Build the per-service tracking rows for orders that predate them.

    Called from both the 1.5.0 migration (upgrade) and the post-init hook (installing onto
    a database that already has orders, e.g. a staging refresh), so an order's progress
    reads the same however the module arrived.

    The order's single staff / processed date / status are copied onto EVERY service of
    the order. An old order never recorded them per service, and the alternative - filling
    only the first service - would make every finished multi-service order in the history
    look half-done and reopen it as pending.

    Plain SQL, not the ORM: this runs over the whole order history in one statement, and
    the status it writes is the status the row's compute would produce anyway.
    """
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pos_order'")
    cols = {r[0] for r in cr.fetchall()}
    if not {"laundry_services", "laundry_status", "laundry_all_done"} <= cols:
        return  # pre-1.5.0 schema; nothing to backfill onto

    # One row per service named in the comma-joined list, in that list's own order.
    # DISTINCT ON guards against a legacy value that repeats a service (the POS
    # de-duplicates, older data is not guaranteed to).
    cr.execute(
        """
        INSERT INTO laundry_order_service
               (order_id, name, sequence, staff_id, processed_datetime, status,
                create_uid, write_uid, create_date, write_date)
        SELECT DISTINCT ON (o.id, btrim(s.name))
               o.id,
               btrim(s.name),
               s.ord * 10,
               o.laundry_staff_id,
               o.laundry_processed_datetime,
               CASE
                   -- The order's recorded status wins, so finished history stays
                   -- finished even where no staff was ever named against it.
                   WHEN o.laundry_status = 'Folded' THEN 'Done'
                   WHEN o.laundry_status = 'In Process' THEN 'In Process'
                   WHEN o.laundry_staff_id IS NOT NULL
                        AND o.laundry_processed_datetime IS NOT NULL THEN 'Done'
                   WHEN o.laundry_staff_id IS NOT NULL THEN 'In Process'
                   ELSE 'Not Started'
               END,
               1, 1, now(), now()
          FROM pos_order o
          CROSS JOIN LATERAL unnest(string_to_array(o.laundry_services, ','))
               WITH ORDINALITY AS s(name, ord)
         WHERE o.laundry_services IS NOT NULL
           AND btrim(s.name) <> ''
           AND NOT EXISTS (
                   SELECT 1 FROM laundry_order_service x
                    WHERE x.order_id = o.id AND x.name = btrim(s.name)
               )
         ORDER BY o.id, btrim(s.name), s.ord
        """
    )
    _logger.info("laundry_pos: backfilled %s service tracking rows", cr.rowcount)

    # The pending roll-up, by the same rule as _compute_laundry_all_done: every service
    # Done, or - for an order with no rows at all - the legacy Wash-Dry-Fold status.
    # bool_and over no rows is NULL, which is what hands those orders to the fallback.
    cr.execute(
        """
        UPDATE pos_order o
           SET laundry_all_done = COALESCE(
                   (SELECT bool_and(s.status = 'Done')
                      FROM laundry_order_service s
                     WHERE s.order_id = o.id),
                   o.laundry_status = 'Folded'
               )
        """
    )
    _logger.info("laundry_pos: recomputed laundry_all_done on %s orders", cr.rowcount)


def _laundry_post_init(env):
    """Copy the legacy Studio workflow fields onto the new module fields on a FRESH
    install. Migrations only run on UPGRADE, so this covers installing laundry_pos onto a
    database that has the x_studio_* fields but not the module yet — e.g. a staging refresh
    from prod (which doesn't have laundry_pos). Values map 1:1; only empty targets are filled.
    """
    cr = env.cr
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pos_order'")
    cols = {r[0] for r in cr.fetchall()}
    mapping = {
        "x_studio_staff": "laundry_staff",
        "x_studio_folding_time": "laundry_processed_datetime",
        "x_studio_status": "laundry_status",
    }
    sets, where = [], []
    for src, dst in mapping.items():
        if src in cols and dst in cols:
            sets.append("%s = COALESCE(%s, %s)" % (dst, src, dst))
            where.append("%s IS NOT NULL" % src)
    if sets:
        cr.execute("UPDATE pos_order SET %s WHERE %s" % (", ".join(sets), " OR ".join(where)))
    # Backfill service type from the legacy x_studio_category service labels.
    if "x_studio_category" in cols and "laundry_service_type" in cols:
        for label, code in LAUNDRY_SERVICE_TYPE_BY_CATEGORY.items():
            cr.execute(
                "UPDATE pos_order SET laundry_service_type = %s "
                "WHERE x_studio_category = %s AND laundry_service_type IS NULL",
                (code, label),
            )
    # Link Staff to the matching employee (by name) for the laundry_staff_id field.
    if "x_studio_staff" in cols and "laundry_staff_id" in cols:
        cr.execute(
            """
            UPDATE pos_order o SET laundry_staff_id = (
                SELECT e.id FROM hr_employee e WHERE e.name = o.x_studio_staff ORDER BY e.id LIMIT 1
            )
            WHERE o.x_studio_staff IS NOT NULL AND o.laundry_staff_id IS NULL
              AND EXISTS (SELECT 1 FROM hr_employee e2 WHERE e2.name = o.x_studio_staff)
            """
        )
        # Aliases: legacy staff name in orders -> current employee name (renamed staff).
        for old_name, emp_name in LAUNDRY_STAFF_ALIASES.items():
            cr.execute(
                """
                UPDATE pos_order o SET laundry_staff_id =
                    (SELECT id FROM hr_employee WHERE name = %s ORDER BY id LIMIT 1)
                WHERE o.x_studio_staff = %s AND o.laundry_staff_id IS NULL
                  AND EXISTS (SELECT 1 FROM hr_employee WHERE name = %s)
                """,
                (emp_name, old_name, emp_name),
            )
    # Archive resigned employees: drops them from the Staff picker, keeps their history.
    cr.execute(
        "UPDATE hr_employee SET active = false WHERE name IN %s AND active = true",
        (LAUNDRY_RESIGNED_EMPLOYEES,),
    )
    # Seed the rider tag on the known riders (configurable via is_laundry_rider afterward).
    if "is_laundry_rider" in env["hr.employee"]._fields:
        cr.execute(
            "UPDATE hr_employee SET is_laundry_rider = true WHERE name IN %s AND is_laundry_rider IS NOT true",
            (LAUNDRY_RIDERS,),
        )
    # Finally, per-service tracking rows for the orders just copied over. Must run AFTER
    # the x_studio_* copy above, since it reads laundry_status / laundry_staff_id /
    # laundry_processed_datetime.
    _laundry_backfill_services(cr)
