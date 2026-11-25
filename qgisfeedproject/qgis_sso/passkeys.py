# coding=utf-8
"""Reading somebody's passkeys, and asking Keycloak to change them.

WebAuthn binds a passkey to the site that created it, so only
``auth.qgis.org`` can register or delete one. This site lists them through the
provisioner service account. Every change goes through an application-initiated
action, which the user authorises at Keycloak.
"""

from datetime import datetime, timezone

from django.utils.translation import gettext_lazy as _

from .keycloak import KeycloakAdminClient
from .models import KeycloakIdentity

#: What Keycloak calls a passkey enrolled under the passwordless policy.
PASSKEY_TYPE = "webauthn-passwordless"

#: Register another one. The same required-action id provisioning sets.
REGISTER = "webauthn-register-passwordless"

#: Set only by a view that has checked the action, so a link cannot choose one.
ACTION_SESSION_KEY = "qgis_sso_kc_action"


class Passkey:
    """One credential, as the profile page shows it."""

    def __init__(self, data: dict) -> None:
        self.id = data.get("id", "")
        self.label = data.get("userLabel") or _("Unnamed passkey")
        # Keycloak counts in milliseconds.
        self.created = data.get("createdDate") or 0
        self.created_at = (
            datetime.fromtimestamp(self.created / 1000, tz=timezone.utc)
            if self.created
            else None
        )


def passkeys_for(
    identity: KeycloakIdentity, client: KeycloakAdminClient | None = None
) -> list[Passkey]:
    """Every passkey on one account, newest first.

    Raises :class:`~qgis_sso.keycloak.KeycloakError` if the realm is unreachable.
    """
    session = client or KeycloakAdminClient()
    return sorted(
        (
            Passkey(credential)
            for credential in session.user_credentials(identity.sub)
            if credential.get("type") == PASSKEY_TYPE
        ),
        key=lambda passkey: passkey.created,
        reverse=True,
    )


def delete_action(credential_id: str) -> str:
    """The action that removes one credential.

    The caller must first check that the credential belongs to the person asking.
    """
    return f"delete_credential:{credential_id}"
