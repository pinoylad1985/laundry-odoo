# `laundry_folding_time` is renamed to `laundry_processed_datetime` (the "Processed Date"
# column) to match the _datetime naming convention.
#
#  1. Rename the pos_order column here — BEFORE the ORM loads the new field — so existing
#     processed-date values on past orders are kept (otherwise the ORM would create a fresh
#     empty column and orphan the old data).
#  2. Rewrite any DB-stored references to the old field NAME (Studio-positioned list columns,
#     saved filters, export templates) so nothing dangles once the field no longer exists.
#     Raw SQL (no ORM) so this runs cleanly before any view is (re)validated on load.
import logging

_logger = logging.getLogger(__name__)

OLD = "laundry_folding_time"
NEW = "laundry_processed_datetime"


def migrate(cr, version):
    if not version:
        return

    # --- 1. Rename the column (preserve data) ---------------------------------------
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'pos_order' AND column_name = %s",
        (OLD,),
    )
    has_old = bool(cr.fetchone())
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'pos_order' AND column_name = %s",
        (NEW,),
    )
    has_new = bool(cr.fetchone())
    if has_old and not has_new:
        cr.execute("ALTER TABLE pos_order RENAME COLUMN %s TO %s" % (OLD, NEW))
        _logger.info("laundry_pos 1.4.12: renamed pos_order.%s -> %s", OLD, NEW)

    # --- 2. Rewrite DB-stored references to the old field NAME -----------------------
    # ir_ui_view.arch_db is a translatable (jsonb) column in Odoo 16+; guard for the
    # (unlikely) plain-text case too.
    cr.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'ir_ui_view' AND column_name = 'arch_db'"
    )
    row = cr.fetchone()
    if row and row[0] == "jsonb":
        cr.execute(
            "UPDATE ir_ui_view "
            "SET arch_db = REPLACE(arch_db::text, %s, %s)::jsonb "
            "WHERE arch_db::text LIKE %s",
            (OLD, NEW, "%" + OLD + "%"),
        )
    else:
        cr.execute(
            "UPDATE ir_ui_view SET arch_db = REPLACE(arch_db, %s, %s) WHERE arch_db LIKE %s",
            (OLD, NEW, "%" + OLD + "%"),
        )
    if cr.rowcount:
        _logger.info("laundry_pos 1.4.12: rewrote %s ir_ui_view arch reference(s)", cr.rowcount)

    # Saved filters (domain / context / sort) + export templates: plain-text columns.
    for table, col in (
        ("ir_filters", "domain"),
        ("ir_filters", "context"),
        ("ir_filters", "sort"),
        ("ir_exports_line", "name"),
    ):
        cr.execute(
            "UPDATE %s SET %s = REPLACE(%s, %%s, %%s) WHERE %s LIKE %%s"
            % (table, col, col, col),
            (OLD, NEW, "%" + OLD + "%"),
        )
        if cr.rowcount:
            _logger.info(
                "laundry_pos 1.4.12: rewrote %s %s.%s reference(s)", cr.rowcount, table, col
            )
