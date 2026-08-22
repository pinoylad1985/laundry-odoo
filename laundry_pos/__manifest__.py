{
    'name': 'Laundry POS',
    'version': '1.4.11',
    'author': 'laundryx',
    'summary': 'Custom laundry service workflow for Point of Sale',
    'category': 'Point of Sale',
    'depends': ['point_of_sale', 'pos_settle_due', 'pos_hr'],
    'data': [
        'security/ir.model.access.csv',
        'data/laundry_service_type_data.xml',
        'data/automations.xml',
        'data/pos_order_filters.xml',
        'views/pos_order_views.xml',
        'views/hr_employee_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'laundry_pos/static/src/**/*.scss',
            'laundry_pos/static/src/**/*.js',
            'laundry_pos/static/src/**/*.xml',
            # Backend-only field widgets must NOT load in the POS bundle.
            ('remove', 'laundry_pos/static/src/backend/**/*'),
        ],
        'web.assets_backend': [
            'laundry_pos/static/src/backend/**/*.scss',
            'laundry_pos/static/src/backend/**/*.js',
            'laundry_pos/static/src/backend/**/*.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'post_init_hook': '_laundry_post_init',
}
