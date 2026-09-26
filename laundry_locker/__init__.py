from . import controllers
from . import models


def post_init_hook(env):
    """Give the push endpoint its secret, and pin the feed URL.

    The token is generated per database rather than shipped: a secret in the
    repository is not a secret. Read it back from Settings > Technical > System
    Parameters (`laundry_locker.push_token`) when configuring the locker
    service that pushes to /laundry_locker/push.
    """
    from .models.laundry_locker_sync import DEFAULT_FIREBASE_URL, PARAM_URL

    env['laundry.locker.transaction']._push_token(generate=True)
    icp = env['ir.config_parameter'].sudo()
    if not icp.get_param(PARAM_URL):
        icp.set_param(PARAM_URL, DEFAULT_FIREBASE_URL)
