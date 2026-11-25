# coding=utf-8
"""Create or link realm accounts, and change accounts once they are bound.

:meth:`Provisioner.inspect` decides without writing anything, and
:meth:`Provisioner.provision` acts on that decision. So the admin's
confirmation page shows exactly what will run.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import tiers
from .keycloak import KeycloakAdminClient, KeycloakError
from .migration import (
    feed_client_id,
    proposed_roles,
    proposed_username,
    required_actions,
)
from .models import KeycloakIdentity, LinkMethod, SsoAuditEvent

CREATE = "create"
LINK = "link"
SKIP = "skip"
ERROR = "error"

#: The feed client's UUID and roles only change when we deploy a role.
CLIENT_CACHE_SECONDS = 600


def _client_cache_key(client_id):
    return f"qgis_sso:client-catalogue:{client_id}"


def clear_client_cache(client_id=None):
    """Forget the cached client UUID and role list.

    Call it after changing roles in the realm. Tests call it too, because the
    cache outlives a test.
    """
    cache.delete(_client_cache_key(client_id or feed_client_id()))


def client_catalogue(client, client_id):
    """``(client_uuid, available_roles)`` for one client, cached."""
    key = _client_cache_key(client_id)
    cached = cache.get(key)
    if cached is not None:
        return cached

    client_uuid = client.client_uuid(client_id)
    catalogue = (client_uuid, client.client_roles(client_uuid))
    cache.set(key, catalogue, CLIENT_CACHE_SECONDS)
    return catalogue


@dataclass
class Decision:
    """What would happen to one user, and why."""

    user: object
    verdict: str = CREATE
    reason: str = ""
    username: str = ""
    email: str = ""
    roles: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    #: The realm account this would bind to, when the verdict is ``LINK``.
    sub: str = ""
    realm_username: str = ""

    @property
    def actionable(self):
        return self.verdict in (CREATE, LINK)

    @property
    def role_labels(self):
        """The roles as a reader sees them, not as Keycloak names them."""
        return tiers.labels(self.roles)

    @property
    def is_create(self):
        return self.verdict == CREATE

    @property
    def is_link(self):
        return self.verdict == LINK

    @property
    def is_skip(self):
        return self.verdict == SKIP

    @property
    def is_error(self):
        return self.verdict == ERROR


class Provisioner:
    """A Keycloak session plus the feed client's role catalogue.

    Build one per run, so the client lookup happens once and not per user.

    Raises :class:`~qgis_sso.keycloak.KeycloakError` if the realm cannot be
    reached or the service account lacks ``view-clients``.
    """

    def __init__(self, client=None):
        self.client = client or KeycloakAdminClient()
        self.client_id = feed_client_id()
        self.client_uuid, self.available_roles = client_catalogue(
            self.client, self.client_id
        )

    def inspect(
        self,
        user,
        flags=(),
        include_flagged=False,
        linked_ids=None,
        can_publish=None,
        linked_subs=None,
    ):
        """Decide about one user without writing anything.

        A caller can resolve ``linked_ids``, ``linked_subs`` and ``can_publish``
        for a whole batch first. That saves queries per user, and a worker
        thread then has nothing left to query.
        """
        decision = Decision(
            user=user,
            username=proposed_username(user),
            email=(user.email or "").strip(),
            flags=list(flags),
        )

        already_linked = (
            user.pk in linked_ids
            if linked_ids is not None
            else KeycloakIdentity.objects.filter(user=user).exists()
        )
        if already_linked:
            return self._skip(decision, _("already has a QGIS account"))
        if not decision.email:
            # The setup link is emailed, so an account with no address is unreachable.
            return self._skip(decision, _("no email address"))
        if decision.flags and not include_flagged:
            return self._skip(decision, _("flagged (%s)") % ", ".join(decision.flags))

        # The realm is shared with hub and plugins, so most people are already in it.
        linked = self._consider_link(
            decision, can_publish=can_publish, linked_subs=linked_subs
        )
        if linked is not None:
            return linked

        try:
            existing = self.client.find_user_by_username(decision.username)
        except KeycloakError as error:
            return self._error(decision, _("lookup failed: %s") % error)

        if existing is not None:
            # Never claim a realm account we did not create. It may be somebody else's.
            return self._skip(
                decision,
                _("a QGIS account named %(username)s already exists. Not claiming it")
                % {"username": decision.username},
            )

        decision.roles = proposed_roles(user, can_publish=can_publish)
        unknown = [role for role in decision.roles if role not in self.available_roles]
        if unknown:
            return self._error(
                decision,
                _(
                    "%(client)s has no roles called %(roles)s yet. Deploy the "
                    "role changes first."
                )
                % {"client": self.client_id, "roles": ", ".join(unknown)},
            )

        return decision

    def _consider_link(self, decision, can_publish=None, linked_subs=None):
        """Whether this account can be bound to one the realm already has.

        We match on an exact address that Keycloak reports as verified, and on
        nothing else, as sign-in linking does. A username proves nothing.

        Returns a decision, or None to go on and consider creating an account.
        """
        try:
            found = self.client.find_user_by_email(decision.email)
        except KeycloakError as error:
            return self._error(decision, _("lookup failed: %s") % error)

        if not found:
            return None

        sub = found.get("id", "")
        if not found.get("emailVerified"):
            return self._skip(
                decision,
                _(
                    "a QGIS account has %(email)s but has not confirmed it. Ask "
                    "them to confirm it first"
                )
                % {"email": decision.email},
            )
        if (found.get("email") or "").strip().lower() != decision.email.lower():
            # Keycloak's exact search is not exact on every version.
            return self._skip(
                decision,
                _("the QGIS account has a different address. Not linking to it"),
            )
        if not sub:
            return self._error(
                decision, _("the QGIS account service gave no account id")
            )
        already_bound = (
            sub in linked_subs
            if linked_subs is not None
            else KeycloakIdentity.objects.filter(sub=sub).exists()
        )
        if already_bound:
            # Every lookup in this app assumes one local account per subject.
            return self._skip(
                decision,
                _("that QGIS account is already linked to another account here"),
            )

        roles = proposed_roles(decision.user, can_publish=can_publish)
        missing = [role for role in roles if role not in self.available_roles]
        if missing:
            # Linking assigns roles, so the client must already have them.
            return self._error(
                decision,
                _(
                    "%(client)s has no roles called %(roles)s yet. Deploy the "
                    "role changes first."
                )
                % {"client": self.client_id, "roles": ", ".join(missing)},
            )

        decision.verdict = LINK
        decision.sub = sub
        decision.realm_username = found.get("username", "")
        decision.roles = roles
        return decision

    def link(self, decision):
        """Bind an account here to one the realm already has.

        It creates nothing in the realm. It assigns the roles the account holds
        here, so the next sign-in mirrors back exactly what it had.

        Raises :class:`~qgis_sso.keycloak.KeycloakError`.
        """
        if decision.roles:
            self.client.assign_client_roles(
                decision.sub,
                self.client_uuid,
                [self.available_roles[role] for role in decision.roles],
            )

        identity = KeycloakIdentity.objects.create(
            user=decision.user,
            sub=decision.sub,
            issuer=issuer_of_record(),
            preferred_username=decision.realm_username or decision.username,
            email_at_link=decision.email,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )
        SsoAuditEvent.record(
            SsoAuditEvent.Action.LINKED,
            user=decision.user,
            sub=decision.sub,
            keycloak_username=decision.realm_username,
            matched_email=decision.email,
            roles=decision.roles,
        )
        return identity

    def provision(self, decision):
        """Create the realm account described by ``decision`` and record it.

        Raises :class:`~qgis_sso.keycloak.KeycloakError`. Callers handle it per
        user, so one failure does not stop the run.
        """
        sub = self.client.create_user(
            {
                "username": decision.username,
                "email": decision.email,
                "firstName": decision.user.first_name,
                "lastName": decision.user.last_name,
                "enabled": decision.user.is_active,
                # Django never verified these addresses.
                "emailVerified": False,
                # No credentials. The account is passkey only.
                "requiredActions": required_actions(),
            }
        )
        if decision.roles:
            self.client.assign_client_roles(
                sub,
                self.client_uuid,
                [self.available_roles[role] for role in decision.roles],
            )

        identity = KeycloakIdentity.objects.create(
            user=decision.user,
            sub=sub,
            issuer=issuer_of_record(),
            preferred_username=decision.username,
            email_at_link=decision.email,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )
        SsoAuditEvent.record(
            SsoAuditEvent.Action.PROVISIONED,
            user=decision.user,
            sub=sub,
            keycloak_username=decision.username,
            roles=decision.roles,
        )
        return identity

    def send_setup_link(self, identity, redirect_uri, lifespan=None):
        """Email one user their account-setup link and record that we did.

        Raises :class:`~qgis_sso.keycloak.KeycloakError`.
        """
        lifespan = lifespan or setup_link_lifespan()
        self.client.execute_actions_email(
            identity.sub,
            required_actions(),
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            lifespan=lifespan,
        )
        # Recorded per user, so a run that stops can pick up where it left off.
        identity.setup_email_sent_at = timezone.now()
        identity.setup_email_send_count += 1
        identity.save(update_fields=["setup_email_sent_at", "setup_email_send_count"])
        SsoAuditEvent.record(
            SsoAuditEvent.Action.SETUP_EMAIL_SENT,
            user=identity.user,
            sub=identity.sub,
            lifespan=lifespan,
            send_count=identity.setup_email_send_count,
        )

    @staticmethod
    def _skip(decision, reason):
        decision.verdict = SKIP
        decision.reason = reason
        return decision

    @staticmethod
    def _error(decision, reason):
        decision.verdict = ERROR
        decision.reason = reason
        return decision


#: Outcomes of :func:`disable_local_password`.
DISABLED = "disabled"
ALREADY_DISABLED = "already-disabled"
NOT_YET_PROVEN = "not-yet-proven"


def disable_local_password(identity) -> str:
    """Make the local password unusable for one linked account.

    Only after a recorded SSO sign-in. Before that, nothing proves the person
    can reach their Keycloak account, and removing the password would lock
    them out. The admin action calls this too, for accounts that signed in
    before it ran on login.
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


#: 14 days. Keycloak's default of 12 hours is too short for a migration wave.
DEFAULT_LIFESPAN_SECONDS = 1209600


def setup_link_lifespan():
    return getattr(settings, "SSO_SETUP_LINK_LIFESPAN", DEFAULT_LIFESPAN_SECONDS)


def issuer_of_record():
    """The issuer to record on an account this module creates.

    It is the issuer every token is checked against, not the admin API
    address. Recording the admin address would refuse the account at its
    first sign-in.
    """
    # Imported here, because qgis_sso.auth imports this module.
    from .auth import expected_issuer

    return expected_issuer()


def setup_redirect_uri():
    """Where Keycloak returns the user after they finish setting up.

    Raises :class:`ValueError` if unset, because Keycloak rejects a setup link
    built without it.
    """
    redirect_uri = getattr(settings, "SSO_SETUP_REDIRECT_URI", "")
    if not redirect_uri:
        raise ValueError(
            _(
                "SSO_SETUP_REDIRECT_URI is not set. It must point at this site, "
                "and it must be a registered redirect URI on the %(client)s "
                "client or Keycloak will reject the link."
            )
            % {"client": feed_client_id()}
        )
    return redirect_uri
