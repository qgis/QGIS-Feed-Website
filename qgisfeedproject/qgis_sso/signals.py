# coding=utf-8
"""Audit receivers for authentication events."""

import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import SsoAuditEvent

logger = logging.getLogger(__name__)

LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"


@receiver(user_logged_in, dispatch_uid="qgis_sso.audit_local_login")
def audit_local_login(sender, request, user, **kwargs) -> None:
    """Record every sign-in that used a local password.

    Local login can be retired once this count reaches zero.
    """
    if getattr(user, "backend", None) != LOCAL_BACKEND:
        return

    logger.warning("Local password sign-in by %r", user.get_username())
    SsoAuditEvent.record(
        SsoAuditEvent.Action.LOCAL_LOGIN,
        user=user,
        has_keycloak_identity=hasattr(user, "keycloak_identity"),
    )
