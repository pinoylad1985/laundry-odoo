{
    'name': 'Laundry Accounting Reports',
    'version': '1.1.0',
    'author': 'laundryx',
    'summary': 'Aged Receivable (Detailed) report, plus an Address column on the standard one',
    'description': """
Adds two things to Accounting > Reporting:

* an **Address** column on the standard Aged Receivable report;
* a separate **Aged Receivable (Detailed)** report with day-by-day aging for the
  first five days overdue (then 6-30 / 31-60 / 61-120 / 121-360 / 360+) and, on
  every open line, the POS order or invoice it came from - date, number and
  laundry service type.

To make that last part possible it also stores a link from each receivable move
line back to its POS order (account.move.line.pos_order_id), which core does not
record for orders paid on Customer Account.
""",
    'category': 'Accounting/Accounting',
    'depends': ['account_reports', 'point_of_sale', 'laundry_pos'],
    'data': [
        'data/aged_receivable_report.xml',
        'data/laundry_aged_receivable_report.xml',
        'data/laundry_aged_receivable_menu.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
