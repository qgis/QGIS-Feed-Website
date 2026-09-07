# coding=utf-8
"""The Keycloak-backed authentication backend.

Two things here are load-bearing and easy to lose in a refactor:

1. Users are matched on the ``sub`` claim and on nothing else. The base class
   matches on email, which would let anyone able to set an address in the
   shared realm take over a site account.
2. A valid Keycloak user with no local account is refused, not created. The
   realm is shared with hub and plugins and is LDAP-federatable, so account
   auto-creation would hand a feed account to the whole directory.
"""

import logging
import time

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.utils import timezone
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from .provisioning import disable_local_password
from .roles import mirror_roles

logger = logging.getLogger(__name__)

# Set on the session when a login is refused because the subject has no
# account here, so the failure page can say why instead of guessing.
REFUSAL_SESSION_KEY = "qgis_sso_refusal"

# Tokens are allowed this much clock skew against our own clock.
LEEWAY_SECONDS = 60


def expected_issuer():
    """The one issuer we accept tokens from."""
    return getattr(
        settings, "SSO_ISSUER", f"{settings.QGIS_AUTH_URL}/realms/qgis"
    ).rstrip("/")


class QGISOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """``sub``-keyed OIDC, with an optional, bounded migration-linking path."""

    # -- token and claim verification ------------------------------------

    def verify_id_token(self, payload):
        """Check the assertions ``mozilla-django-oidc`` does not check itself.

        The library verifies the ID token's signature and nonce but not its
        ``iss``, ``aud`` or ``exp``. Without the audience check a token minted
        for a different client in the same realm - hub or plugins - would be
        accepted here, which is exactly what the migration plan requires us to
        reject.
        """
        if not isinstance(payload, dict):
            raise SuspiciousOperation("ID token payload is not an object")

        issuer = (payload.get("iss") or "").rstrip("/")
        if issuer != expected_issuer():
            raise SuspiciousOperation(
                f"ID token issuer {issuer!r} is not {expected_issuer()!r}"
            )

        # 'aud' is a string or a list of strings; our client id must be in it.
        audience = payload.get("aud") or []
        if isinstance(audience, str):
            audience = [audience]
        if settings.OIDC_RP_CLIENT_ID not in audience:
            raise SuspiciousOperation(
                f"ID token audience {audience!r} does not include this client"
            )

        # When more than one audience is present the spec requires azp, and
        # requires it to be us.
        if len(audience) > 1:
            authorized_party = payload.get("azp")
            if authorized_party and authorized_party != settings.OIDC_RP_CLIENT_ID:
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

    def verify_claims(self, claims):
        """Userinfo claims must carry a subject.

        The issuer and audience are asserted against the ID token instead:
        Keycloak's userinfo response carries neither, so checking them here
        would either be vacuous or would reject every login.
        """
        return bool(claims.get("sub"))

    # -- user resolution --------------------------------------------------

    def filter_users_by_claims(self, claims):
        """Resolve the subject to a user, on ``sub`` alone.

        Deliberately no email fallback. A user whose username *and* email have
        both changed in Keycloak still matches here, which is the entire point
        of keying on the subject.
        """
        sub = claims.get("sub")
        if not sub:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(keycloak_identity__sub=sub)

    def create_user(self, claims):
        """Never create an account from a token.

        ``OIDC_CREATE_USER = False`` should mean this is never reached; it is
        overridden anyway so that flipping the setting by accident cannot open
        the site to every user in the shared realm.
        """
        raise SuspiciousOperation(
            "Refusing to create a site account from a Keycloak token. "
            "Accounts are created by invitation only."
        )

    def get_or_create_user(self, access_token, id_token, payload):
        """Resolve the token to a user, or refuse.

        Overridden rather than relying on the base implementation because the
        migration-linking path has to sit between "no user matched this
        subject" and "give up", and because the base class would otherwise
        consult ``OIDC_CREATE_USER`` at that point.
        """
        self.verify_id_token(payload)

        claims = self.get_userinfo(access_token, id_token, payload)
        if not self.verify_claims(claims):
            raise SuspiciousOperation("Claims verification failed")

        # The userinfo response and the ID token must describe one person.
        # They are fetched over separate requests, so this is cheap insurance
        # against a mix-up rather than a theoretical concern.
        if claims.get("sub") != payload.get("sub"):
            raise SuspiciousOperation("Userinfo subject does not match the ID token")

        users = list(self.filter_users_by_claims(claims))
        if len(users) > 1:
            # Unreachable while sub is unique, which is why it is worth
            # shouting about if it ever happens.
            raise SuspiciousOperation("More than one user matched one subject")
        if len(users) == 1:
            return self.update_user(users[0], claims)

        user = self.link_migrated_user(claims)
        if user is not None:
            return self.update_user(user, claims)

        self.refuse(claims)
        return None

    # -- migration linking -------------------------------------------------

    def link_migrated_user(self, claims):
        """Bind a pre-existing local account to a subject, or return None.

        Only active during the migration window, and only on an exact match of
        *both* username and verified email. A single-field match is a refusal,
        not a guess: usernames are not unique across systems and an
        unverified email is an assertion by whoever registered it.
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
            # The fallback exists for accounts that have not migrated yet, not
            # as a standing second front door for those that have.
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

    # -- refusal ------------------------------------------------------------

    def refuse(self, claims):
        """Record and explain a login by a realm user with no account here."""
        sub = claims.get("sub", "")
        username = claims.get("preferred_username", "")
        # The subject is logged. The tokens are not, ever - they are bearer
        # credentials and a log file is not a place to keep them.
        logger.warning(
            "Refused sign-in for subject %s (%r): no account on this site",
            sub,
            username,
        )
        SsoAuditEvent.record(SsoAuditEvent.Action.REFUSED, sub=sub, username=username)
        request = getattr(self, "request", None)
        if request is not None and hasattr(request, "session"):
            request.session[REFUSAL_SESSION_KEY] = "no-account"

    # -- per-login updates ---------------------------------------------------

    def update_user(self, user, claims):
        """Refresh the identity record and reconcile permissions."""
        # Fetched rather than read off ``user.keycloak_identity``: after
        # link_migrated_user the reverse one-to-one cache is a Django
        # implementation detail to be relying on, and this costs the same
        # query the attribute access would have made anyway.
        identity = KeycloakIdentity.objects.get(user=user)

        issuer = expected_issuer()
        if identity.issuer and identity.issuer.rstrip("/") != issuer:
            # The subject namespace belongs to the issuer; the same sub from a
            # different realm is a different person.
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

        # This sign-in is the proof the account is reachable through Keycloak,
        # which is the exact condition for retiring its password. Doing it here
        # closes the window in which both a password and SSO work for the same
        # account, rather than leaving it open until somebody runs a batch.
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


def provider_logout(request):
    """Build the Keycloak RP-initiated logout URL.

    Without this, signing out of Django leaves the Keycloak session live and
    the next sign-in completes silently with no credential prompt, which looks
    exactly like the logout having failed.
    """
    from urllib.parse import urlencode

    id_token = request.session.get("oidc_id_token")
    redirect_uri = request.build_absolute_uri(
        getattr(settings, "LOGOUT_REDIRECT_URL", "/") or "/"
    )
    query = {"post_logout_redirect_uri": redirect_uri}
    if id_token:
        query["id_token_hint"] = id_token
    else:
        # Keycloak requires either id_token_hint or client_id to honour the
        # post-logout redirect; without one it lands on a confirmation page.
        query["client_id"] = settings.OIDC_RP_CLIENT_ID
    return f"{expected_issuer()}/protocol/openid-connect/logout?{urlencode(query)}"
