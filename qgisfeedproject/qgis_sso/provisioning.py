# coding=utf-8
"""Provisioning a Django user into Keycloak, once, for both callers.

Who may be provisioned, what roles they get and what the realm account looks
like are decided here rather than in the admin action that calls it, so the
rules stay testable without a browser and survive a second caller.

The split is deliberate: :meth:`Provisioner.inspect` reads and decides but
writes nothing, and :meth:`Provisioner.provision` acts on a decision it is
handed. That is what lets the admin show an accurate confirmation page without
a preview path that could drift from what actually happens.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .keycloak import KeycloakAdminClient, KeycloakError
from .migration import (
    feed_client_id,
    proposed_roles,
    proposed_username,
    required_actions,
)
from .models import KeycloakIdentity, LinkMethod, SsoAuditEvent

CREATE = "create"
SKIP = "skip"
ERROR = "error"


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

    @property
    def actionable(self):
        return self.verdict == CREATE

    @property
    def is_skip(self):
        return self.verdict == SKIP

    @property
    def is_error(self):
        return self.verdict == ERROR


class Provisioner:
    """A Keycloak session plus the feed client's role catalogue.

    Constructed once per run: the client lookup and role list are two HTTP
    round trips that must not be repeated per user.

    Raises :class:`~qgis_sso.keycloak.KeycloakError` if the realm cannot be
    reached or the service account lacks ``view-clients``.
    """

    def __init__(self, client=None):
        self.client = client or KeycloakAdminClient()
        self.client_id = feed_client_id()
        self.client_uuid = self.client.client_uuid(self.client_id)
        self.available_roles = self.client.client_roles(self.client_uuid)

    def inspect(
        self, user, flags=(), include_flagged=False, linked_ids=None, roles=None
    ):
        """Decide about one user without writing anything.

        ``linked_ids`` lets a caller pass a precomputed set of already-linked
        user ids rather than issuing a query per user.

        ``roles`` overrides what the account would get. Migration derives roles
        from the Django groups an account already has; somebody arriving on an
        invitation has none, and the invitation says what they were offered.
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
            return self._skip(decision, _("already has a Keycloak identity"))
        if not decision.email:
            # Provisioning without an address produces an account nobody can
            # ever reach, because the setup link is emailed.
            return self._skip(decision, _("no email address"))
        if decision.flags and not include_flagged:
            return self._skip(decision, _("flagged (%s)") % ", ".join(decision.flags))

        try:
            existing = self.client.find_user_by_username(decision.username)
        except KeycloakError as error:
            return self._error(decision, _("lookup failed: %s") % error)

        if existing is not None:
            # Never claim a realm account we did not create. It may belong to
            # somebody else entirely.
            return self._skip(
                decision,
                _("the realm already has a user named %(username)s; not claiming it")
                % {"username": decision.username},
            )

        decision.roles = list(roles) if roles is not None else proposed_roles(user)
        unknown = [role for role in decision.roles if role not in self.available_roles]
        if unknown:
            return self._error(
                decision,
                _(
                    "client %(client)s has no role(s) %(roles)s. Deploy the realm changes first."
                )
                % {"client": self.client_id, "roles": ", ".join(unknown)},
            )

        return decision

    def provision(
        self,
        decision,
        sponsor=None,
        link_method=LinkMethod.PRE_SSO_MIGRATION,
    ):
        """Create the realm account described by ``decision`` and record it.

        ``sponsor`` and ``link_method`` are written in the same INSERT as the
        rest, not set afterwards. The check constraint on the identity fires at
        insert time, so an identity that acquires its sponsor a line later is
        one the database has already refused.

        Raises :class:`~qgis_sso.keycloak.KeycloakError`. Callers handle that
        per user so that one failure does not abandon the rest of the run.
        """
        sub = self.client.create_user(
            {
                "username": decision.username,
                "email": decision.email,
                "firstName": decision.user.first_name,
                "lastName": decision.user.last_name,
                "enabled": decision.user.is_active,
                # Django never verified these addresses, and the whole
                # migration hangs off email reaching the right person.
                "emailVerified": False,
                # No credentials, ever: the account is passkey-only, so there
                # is no password for us to choose or for anyone to steal.
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
            issuer=f"{self.client.server_url}/realms/{self.client.realm}",
            preferred_username=decision.username,
            email_at_link=decision.email,
            link_method=link_method,
            sponsor=sponsor,
        )
        SsoAuditEvent.record(
            SsoAuditEvent.Action.PROVISIONED,
            user=decision.user,
            sub=sub,
            keycloak_username=decision.username,
            roles=decision.roles,
            sponsor=getattr(sponsor, "username", None),
        )
        return identity

    def send_setup_link(self, identity, redirect_uri, lifespan=None):
        """Email one user their account-setup link and record that we did.

        Raises :class:`~qgis_sso.keycloak.KeycloakError`.
        """
        self.client.execute_actions_email(
            identity.sub,
            required_actions(),
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            lifespan=lifespan or setup_link_lifespan(),
        )
        # Recorded per user so a run is resumable and a resend is a
        # deliberate act rather than the default.
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

    def issue_setup_link(self, identity, redirect_uri, lifespan=None):
        """Return one user's sign-in link for an administrator to hand over.

        Following it signs that person in, at which point Keycloak presents
        whichever required actions are still outstanding on the account - the
        email verification and the passkey enrolment that provisioning set. So
        it reaches the same place the emailed link does, by a different route.

        It is returned and never stored: anyone holding it can sign in as that
        person, which is why the audit event below records that a link was
        issued and not the link.

        Raises :class:`~qgis_sso.keycloak.KeycloakError`.
        """
        username = identity.preferred_username or proposed_username(identity.user)
        user_id, link = self.client.magic_link(
            username,
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            lifespan=lifespan or setup_link_lifespan(),
        )
        if user_id != identity.sub:
            # The endpoint is keyed on the username; everything else here is
            # keyed on the subject. A username that resolved to somebody else
            # would sign the wrong person into this account.
            raise KeycloakError(
                f"Refusing the link: the realm answered for subject {user_id!r}, "
                f"not the one bound to {identity.user.username!r}."
            )
        identity.setup_link_issued_at = timezone.now()
        identity.setup_link_issue_count += 1
        identity.save(update_fields=["setup_link_issued_at", "setup_link_issue_count"])
        SsoAuditEvent.record(
            SsoAuditEvent.Action.SETUP_LINK_ISSUED,
            user=identity.user,
            sub=identity.sub,
            lifespan=lifespan,
            issue_count=identity.setup_link_issue_count,
        )
        return link

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


def disable_local_password(identity):
    """Make the local Django password unusable for one linked account.

    The gate is a *recorded successful SSO login*, not merely having been
    provisioned. Taking somebody's password away before they have signed in
    through Keycloak once is how people get locked out of an account they can
    no longer recover on their own - and with passkey-only accounts there is
    no password reset to fall back on either.

    Called both from the login path, where that condition has just become
    true, and from the admin action, which backfills accounts that signed in
    before this was automatic.
    """
    if identity.first_sso_login_at is None:
        return NOT_YET_PROVEN

    user = identity.user
    if not user.has_usable_password():
        # Already unusable, most likely from migration linking. Record the
        # fact so it stops appearing as outstanding.
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


#: 14 days. Keycloak's default is 12 hours, which is far too short for a
#: migration wave - people are on holiday.
DEFAULT_LIFESPAN_SECONDS = 1209600


def setup_link_lifespan():
    return getattr(settings, "SSO_SETUP_LINK_LIFESPAN", DEFAULT_LIFESPAN_SECONDS)


def setup_redirect_uri():
    """Where Keycloak returns the user after they finish setting up.

    Raises :class:`ValueError` if unset, because a setup link built without it
    is one Keycloak will reject after the email has already gone out.
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
