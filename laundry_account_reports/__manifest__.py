{
    'name': 'Laundry Accounting Reports',
    'version': '1.2.0',
    'author': 'laundryx',
    'summary': 'Aged Receivable (Detailed) report: per-order aging with document and address columns',
    'description': """
Adds an **Aged Receivable (Detailed)** report to Accounting > Reporting, next to
the standard Aged Receivable, which is left exactly as Odoo ships it.

It ages day by day for the first five days overdue (then 6-30 / 31-60 / 61-120 /
121-360 / 360+) and shows, on every open line, the POS order or invoice it came
from - date, number and laundry service type - plus the customer's address.

To make that possible it also stores a link from each receivable move line back
to its POS order (account.move.line.pos_order_id), which core does not record
for orders paid on Customer Account.
""",
    'category': 'Accounting/Accounting',
    'depends': ['account_reports', 'point_of_sale', 'laundry_pos'],
    'data': [
        'data/laundry_aged_receivable_report.xml',
        'data/laundry_aged_receivable_menu.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
