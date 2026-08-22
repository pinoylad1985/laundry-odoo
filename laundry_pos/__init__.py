from . import models

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
