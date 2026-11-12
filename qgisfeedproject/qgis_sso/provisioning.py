# coding=utf-8
"""Changes this site makes to an account once it is bound to the realm."""

from django.utils import timezone

from .models import SsoAuditEvent

#: Outcomes of :func:`disable_local_password`.
DISABLED = "disabled"
ALREADY_DISABLED = "already-disabled"
NOT_YET_PROVEN = "not-yet-proven"


def disable_local_password(identity) -> str:
    """Make the local password unusable for one linked account.

    Only after a recorded SSO sign-in. Before that, nothing proves the person
    can reach their Keycloak account, and removing the password would lock
    them out.
    """
    if not identity.has_logged_in_via_sso:
        return NOT_YET_PROVEN

    user = identity.user
    if not user.has_usable_password():
        if identity.local_password_disabled_at is None:
            identity.local_password_disabled_at = timezone.now()
            identity.save(update_fields=["local_password_disabled_at"])
        return ALREADY_DISABLED

    user.set_unusable_password()
    user.save(update_fields=["password"])
    identity.local_password_disabled_at = timezone.now()
    identity.save(update_fields=["local_password_disabled_at"])
    SsoAuditEvent.record(
        SsoAuditEvent.Action.LOCAL_PASSWORD_DISABLED, user=user, sub=identity.sub
    )
    return DISABLED
