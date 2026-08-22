# The "Processed Date" field widget is renamed laundry_folding_pin -> laundry_processed_date_pin
# (cosmetic: keeps the widget name in step with the field). Our own module view is reloaded
# from XML with the new name, but a Studio-positioned copy of the column could carry the old
# widget attribute in its stored arch -- rewrite it so the picker/PIN widget keeps rendering
# instead of silently falling back to a plain datetime field. No-op if the old name is absent.
import logging

_logger = logging.getLogger(__name__)

OLD = "laundry_folding_pin"
NEW = "laundry_processed_date_pin"


def migrate(cr, version):
    if not version:
        return
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
        _logger.info("laundry_pos 1.4.13: rewrote %s widget reference(s) in view arch", cr.rowcount)
