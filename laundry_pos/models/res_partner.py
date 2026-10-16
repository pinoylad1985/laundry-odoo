import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def phone_last10(value):
    """The comparison key for a phone number: its last 10 digits.

    The same person writes their number down as `09171234567`, `+63 917 123
    4567` or `917 123 4567` on different days, and a cashier re-typing it at
    the counter spells it a fourth way. The last 10 digits are the part that
    identifies a PH mobile (the subscriber number, without the trunk 0 or the
    +63), so that is what the customer book is keyed on - the same key
    laundry_locker matches its transactions to a customer on.
    """
    digits = re.sub('[^0-9]', '', value or '')
    return digits[-10:] if len(digits) >= 10 else ''


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Comma-joined tag names, computed on the partner itself so the POS receipt
    # can show them WITHOUT loading the res.partner.category model. Relational
    # loading only pulls categories referenced by pre-loaded partners, so tags
    # on customers fetched on-demand (cashier search) would otherwise be missing.
    pos_tag_names = fields.Char(
        string="POS Tag Names",
        compute="_compute_pos_tag_names",
    )

    @api.depends('category_id', 'category_id.name')
    def _compute_pos_tag_names(self):
        for partner in self:
            partner.pos_tag_names = ", ".join(partner.category_id.mapped('name'))

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if 'pos_tag_names' not in fields_list:
            fields_list.append('pos_tag_names')
        return fields_list

    # --- One customer per phone number -----------------------------------
    #
    # A second contact for the same person splits their orders, their dues and
    # their history across two records, and nothing afterwards puts them back
    # together: the counter sees half a customer whichever one it opens. The
    # number is the one thing a laundry customer always gives, so it is what
    # the book is kept unique on.

    def _laundry_phone_twin(self, phone, exclude_id=None):
        """The partner that already holds this number, or an empty recordset.

        Matched in SQL on the DIGITS of the stored number rather than through
        the ORM, because the book holds the same number in several spellings
        and no domain can compare them: `0917 123 4567` and `+639171234567`
        are one number, and only stripping both to digits says so.

        ARCHIVED partners count. An archived duplicate is invisible to the
        cashier's search, so skipping them is exactly how a second record gets
        created for someone who is already in the book - the message below
        says when the twin is archived so the cashier knows to restore it
        rather than hunt for something they cannot see.

        CHILD contacts (a delivery address under a company) are left out on
        both sides: they legitimately carry their parent's number, and
        blocking that would stop addresses being added at all.
        """
        key = phone_last10(phone)
        if not key:
            return self.browse()
        query = """
            SELECT id
              FROM res_partner
             WHERE parent_id IS NULL
               AND regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g') LIKE %s
        """
        params = ['%' + key]
        if exclude_id:
            query += " AND id != %s"
            params.append(exclude_id)
        query += " ORDER BY id LIMIT 1"
        self.env.cr.execute(query, params)
        row = self.env.cr.fetchone()
        # sudo for the name only: the twin may sit outside what this user is
        # allowed to read, and "it exists but I cannot tell you which" is not
        # an answer anyone can act on.
        return self.env['res.partner'].sudo().browse(row[0]) if row else self.browse()

    def _laundry_check_phone_duplicate(self, phone, exclude_id=None):
        twin = self._laundry_phone_twin(phone, exclude_id=exclude_id)
        if not twin:
            return
        raise ValidationError(_(
            "This phone number is already used by %(name)s%(archived)s. "
            "Open that customer instead of creating a new one - two contacts "
            "for one person split their orders, points and dues between them.",
            name=twin.display_name,
            archived='' if twin.active else _(' (archived - restore it to use it)'),
        ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('phone') and not vals.get('parent_id'):
                self._laundry_check_phone_duplicate(vals['phone'])
        return super().create(vals_list)

    def write(self, vals):
        # Checked on write as well, or the block is a formality: creating the
        # customer with no number and typing it in a second later would walk
        # straight past it. Only when the number itself is being set, and
        # never against the record's own number.
        if vals.get('phone'):
            for partner in self:
                if not vals.get('parent_id', partner.parent_id):
                    self._laundry_check_phone_duplicate(
                        vals['phone'], exclude_id=partner.id
                    )
        return super().write(vals)
