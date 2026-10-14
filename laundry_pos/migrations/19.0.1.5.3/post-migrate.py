# 161 orders (2025-10-12 .. 2025-10-16) carry laundry_status = 'Done', which is not one of
# the field's stored values - it is the LABEL of 'Folded'. It came in from the legacy
# Studio x_studio_status, which used the label as its value for that one window, and the
# 1.2.3 copy took it verbatim.
#
# It broke two things at once, because both ask "is this value one I know?":
#   * Orders Pending read it as unfinished (the roll-up tests for 'Folded'), so a batch of
#     finished orders from a year ago reappeared as pending.
#   * The readonly Selection widget looks the label up as options.find(...)[1] and threw
#     "Cannot read properties of undefined (reading '1')", taking the order form down.
#
# Rewriting the value is the fix - these orders ARE finished - and the roll-up is then
# recomputed over the whole history by the new rule (see _compute_laundry_all_done).
import logging

from odoo.addons.laundry_pos import _laundry_normalize_status, _laundry_recompute_all_done

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _laundry_normalize_status(cr)
    _laundry_recompute_all_done(cr)
