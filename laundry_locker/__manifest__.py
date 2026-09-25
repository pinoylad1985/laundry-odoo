{
    'name': 'Laundry Lockers',
    'version': '1.0.0',
    'author': 'laundryx',
    'summary': 'PudoPro locker transactions, and billing them through the POS',
    'description': """
Mirrors the PudoPro locker transactions that feed the Locker tab of
dashboard.laundryx.app into Odoo, so a cashier can turn one into a POS order
without re-typing the customer.

Each transaction is matched against the customer book on the LAST 10 DIGITS of
its phone number and shown as Returning or New, and carries a Billed flag with
a link to the POS order it was billed on.
""",
    'category': 'Point of Sale',
    'depends': ['laundry_pos'],
    'data': [
        'security/ir.model.access.csv',
        'data/laundry_locker_cron.xml',
        'views/laundry_locker_transaction_views.xml',
        'views/laundry_locker_menus.xml',
        'views/pos_order_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'laundry_locker/static/src/**/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
