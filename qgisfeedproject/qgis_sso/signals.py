# coding=utf-8
"""Audit receivers for authentication events."""

import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import SsoAuditEvent

logger = logging.getLogger(__name__)

#: The backend path that means "authenticated with a local Django password".
LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"


@receiver(user_logged_in, dispatch_uid="qgis_sso.audit_local_login")
def audit_local_login(sender, request, user, **kwargs):
    """Record every sign-in that used a local password.

    The decision to retire local login is gated on this count reaching zero,
    so the count has to exist. Logged at WARNING deliberately: during the
    migration window a local sign-in is a thing somebody should look at, not
    routine traffic.
    """
    backend = getattr(user, "backend", "") or ""
    if backend != LOCAL_BACKEND:
        return

    logger.warning(
        "Local password sign-in by %r - this path is temporary and is being retired",
        user.get_username(),
    )
    SsoAuditEvent.record(
        SsoAuditEvent.Action.LOCAL_LOGIN,
        user=user,
        has_keycloak_identity=hasattr(user, "keycloak_identity"),
    )
