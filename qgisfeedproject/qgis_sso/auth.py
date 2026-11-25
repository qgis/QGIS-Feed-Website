# coding=utf-8
"""The Keycloak authentication backend.

Users are matched on the ``sub`` claim only. The base class matches on email,
which would let anyone who can set an address in the shared realm take over a
site account.

A realm user with no account here is refused, never created. The realm is
shared with hub and plugins and federates LDAP, so creating accounts would give
the whole directory a feed account.
"""

import logging
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.utils import timezone
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from .provisioning import disable_local_password
from .roles import mirror_roles

logger = logging.getLogger(__name__)

# Tells the failure page that the subject has no account here.
REFUSAL_SESSION_KEY = "qgis_sso_refusal"

# Clock skew allowed on a token's expiry.
LEEWAY_SECONDS = 60


def expected_issuer() -> str:
    """The one issuer we accept tokens from."""
    return settings.SSO_ISSUER.rstrip("/")


class QGISOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """OIDC keyed on ``sub``, with an optional migration-linking path."""

    def verify_id_token(self, payload):
        """Check the claims ``mozilla-django-oidc`` leaves unchecked.

        The library verifies the signature and nonce, but not ``iss``, ``aud``
        or ``exp``. Without the audience check, a token minted for hub or
        plugins in the same realm would be accepted here.
        """
        if not isinstance(payload, dict):
            raise SuspiciousOperation("ID token payload is not an object")

        issuer = (payload.get("iss") or "").rstrip("/")
        if issuer != expected_issuer():
            raise SuspiciousOperation(
                f"ID token issuer {issuer!r} is not {expected_issuer()!r}"
            )

        audience = payload.get("aud") or []
        if isinstance(audience, str):
            audience = [audience]
        if settings.OIDC_RP_CLIENT_ID not in audience:
            raise SuspiciousOperation(
                f"ID token audience {audience!r} does not include this client"
            )

        # With several audiences, azp must name us when it is present.
        authorized_party = payload.get("azp")
        if (
            len(audience) > 1
            and authorized_party
            and authorized_party != settings.OIDC_RP_CLIENT_ID
        ):
            raise SuspiciousOperation(
                f"ID token azp {authorized_party!r} is not this client"
            )

        expiry = payload.get("exp")
        if expiry is None:
            raise SuspiciousOperation("ID token has no expiry")
        if float(expiry) + LEEWAY_SECONDS < time.time():
            raise SuspiciousOperation("ID token has expired")

        if not payload.get("sub"):
            raise SuspiciousOperation("ID token has no subject")

        return payload

    def get_user(self, user_id):
        """Resolve a session to its user, refusing an inactive account.

        The library skips the ``is_active`` check that ``ModelBackend`` makes,
        so a deactivated account would stay signed in until its session ends.
        """
        user = super().get_user(user_id)
        if user is not None and not user.is_active:
            return None
        return user

    def verify_claims(self, claims):
        """Userinfo claims must carry a subject.

        Keycloak's userinfo response has no issuer or audience, so those are
        checked on the ID token instead.
        """
        return bool(claims.get("sub"))

    def filter_users_by_claims(self, claims):
        """Find the user bound to this subject. There is no email fallback."""
        sub = claims.get("sub")
        if not sub:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(keycloak_identity__sub=sub)

    def create_user(self, claims):
        """Refuse, even if ``OIDC_CREATE_USER`` is switched on by mistake."""
        raise SuspiciousOperation(
            "Refusing to create a site account from a Keycloak token. "
            "Accounts are created by invitation only."
        )

    def get_or_create_user(self, access_token, id_token, payload):
        """Resolve the token to a user, or refuse.

        Overridden so that migration linking runs between "no user matched"
        and "give up", and so that ``OIDC_CREATE_USER`` is never consulted.
        """
        self.verify_id_token(payload)

        claims = self.get_userinfo(access_token, id_token, payload)
        if not self.verify_claims(claims):
            raise SuspiciousOperation("Claims verification failed")
        if claims.get("sub") != payload.get("sub"):
            raise SuspiciousOperation("Userinfo subject does not match the ID token")

        user = self.filter_users_by_claims(claims).first()
        if user is None:
            user = self.link_migrated_user(claims)
        if user is None:
            self.refuse(claims)
            return None
        return self.update_user(user, claims)

    def link_migrated_user(self, claims):
        """Bind an existing local account to this subject, or return None.

        Only while ``SSO_MIGRATION_LINKING`` is on, and only when both the
        username and a verified email match exactly one unlinked account.
        """
        if not getattr(settings, "SSO_MIGRATION_LINKING", False):
            return None

        username = (claims.get("preferred_username") or "").strip()
        email = (claims.get("email") or "").strip()
        if not username or not email:
            return None
        if claims.get("email_verified") is not True:
            logger.info(
                "Not linking %r: Keycloak has not verified the address", username
            )
            return None

        candidates = list(
            self.UserModel.objects.filter(
                is_active=True,
                username__iexact=username,
                email__iexact=email,
                keycloak_identity__isnull=True,
            )[:2]
        )
        if len(candidates) != 1:
            logger.warning(
                "Not linking %r: %d unlinked active accounts match on username "
                "and verified email",
                username,
                len(candidates),
            )
            return None

        user = candidates[0]
        with transaction.atomic():
            KeycloakIdentity.objects.create(
                user=user,
                sub=claims["sub"],
                issuer=expected_issuer(),
                preferred_username=username,
                email_at_link=email,
                link_method=LinkMethod.PRE_SSO_MIGRATION,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            SsoAuditEvent.record(
                SsoAuditEvent.Action.LINKED,
                user=user,
                sub=claims["sub"],
                link_method=LinkMethod.PRE_SSO_MIGRATION.value,
                email=email,
            )
        logger.info("Linked local account %r to subject %s", username, claims["sub"])
        return user

    def refuse(self, claims):
        """Record and explain a sign-in by a realm user with no account here."""
        sub = claims.get("sub", "")
        username = claims.get("preferred_username", "")
        # Log the subject, never the tokens.
        logger.warning(
            "Refused sign-in for subject %s (%r): no account on this site",
            sub,
            username,
        )
        SsoAuditEvent.record(SsoAuditEvent.Action.REFUSED, sub=sub, username=username)
        request = getattr(self, "request", None)
        if request is not None and hasattr(request, "session"):
            request.session[REFUSAL_SESSION_KEY] = "no-account"

    def update_user(self, user, claims):
        """Refresh the identity record and reconcile permissions."""
        identity = KeycloakIdentity.objects.get(user=user)

        issuer = expected_issuer()
        if identity.issuer and identity.issuer.rstrip("/") != issuer:
            # Both values are logged, to tell a stale QGIS_AUTH_URL from another realm.
            logger.warning(
                "Refusing %s: identity carries issuer %r, this token came from %r",
                identity.sub,
                identity.issuer,
                issuer,
            )
            raise SuspiciousOperation(
                "Subject is linked to a different issuer than the one that "
                "issued this token"
            )

        roles = [
            role for role in (claims.get("feed_roles") or []) if isinstance(role, str)
        ]
        changed = mirror_roles(user, roles)

        now = timezone.now()
        identity.issuer = issuer
        identity.last_seen_roles = roles
        identity.preferred_username = claims.get("preferred_username") or ""
        identity.last_sso_login_at = now
        if identity.first_sso_login_at is None:
            identity.first_sso_login_at = now
        identity.save(
            update_fields=[
                "issuer",
                "last_seen_roles",
                "preferred_username",
                "last_sso_login_at",
                "first_sso_login_at",
            ]
        )

        # This sign-in proves the account is reachable, so the password can go.
        if getattr(settings, "SSO_RETIRE_PASSWORD_ON_LOGIN", True):
            disable_local_password(identity)

        if changed.keys() - {"roles"}:
            SsoAuditEvent.record(
                SsoAuditEvent.Action.ROLES_MIRRORED,
                user=user,
                sub=identity.sub,
                **changed,
            )
        SsoAuditEvent.record(
            SsoAuditEvent.Action.SSO_LOGIN, user=user, sub=identity.sub, roles=roles
        )
        return user


def provider_logout(request) -> str:
    """Build the Keycloak logout URL, so signing out also ends that session."""
    query = {
        "post_logout_redirect_uri": request.build_absolute_uri(
            getattr(settings, "LOGOUT_REDIRECT_URL", "/") or "/"
        )
    }
    id_token = request.session.get("oidc_id_token")
    if id_token:
        query["id_token_hint"] = id_token
    else:
        # Without a hint or a client id, Keycloak shows a confirmation page.
        query["client_id"] = settings.OIDC_RP_CLIENT_ID
    return f"{expected_issuer()}/protocol/openid-connect/logout?{urlencode(query)}"
