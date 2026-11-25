# coding=utf-8
"""Reading somebody's passkeys, and asking Keycloak to change them.

A passkey belongs to ``auth.qgis.org``, not to this site: WebAuthn binds every
credential to the relying party that created it, so a browser will not create
or use one for ``feed.qgis.org`` on Keycloak's behalf. Registration therefore
happens at Keycloak whatever the page around it looks like.

What this site can do is show somebody what they have and take them to the
right place to change it, by way of an application-initiated action: an
ordinary sign-in request carrying ``kc_action``, which Keycloak answers by
running that action and returning them here afterwards.

Reads go through the provisioner service account. Writes never do - the user
authorises those at Keycloak themselves - so nothing here can alter a
credential even when handed a request that asks it to.
"""

from datetime import datetime, timezone

from django.utils.translation import gettext_lazy as _

from .keycloak import KeycloakAdminClient

#: What Keycloak calls a passkey enrolled under the passwordless policy.
PASSKEY_TYPE = "webauthn-passwordless"

#: Register another one. The same required-action id provisioning sets.
REGISTER = "webauthn-register-passwordless"

#: Session key carrying the action into the sign-in request, put there by a
#: view that has already checked it so a crafted link cannot choose its own.
ACTION_SESSION_KEY = "qgis_sso_kc_action"


class Passkey:
    """One credential, as the profile page shows it."""

    def __init__(self, data):
        self.id = data.get("id", "")
        self.label = data.get("userLabel") or _("Unnamed passkey")
        # Keycloak counts in milliseconds; Django templates want a datetime.
        self.created = data.get("createdDate") or 0
        self.created_at = (
            datetime.fromtimestamp(self.created / 1000, tz=timezone.utc)
            if self.created
            else None
        )


def passkeys_for(identity, client=None):
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


def delete_action(credential_id):
    """The action that removes one credential.

    The caller must have checked it belongs to the person asking. Keycloak
    would refuse somebody else's, but relying on that means sending their
    identifier to find out.
    """
    return f"delete_credential:{credential_id}"
