import logging

from . import models
from . import wizard

_logger = logging.getLogger(__name__)


def post_init_sync_integrations(env):
    """Post-init hook to sync integration modules after data load."""
    try:
        env['approval.integration.module'].sudo().sync_integrations()
    except Exception as exc:
        _logger.warning('Failed to sync approval integrations during post-init: %s', exc)

