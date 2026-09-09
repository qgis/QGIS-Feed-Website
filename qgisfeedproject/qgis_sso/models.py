# coding=utf-8
"""Identity, trust and audit models for Keycloak single sign-on."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class LinkMethod(models.TextChoices):
    """How a Django account came to be bound to a Keycloak identity.

    Recorded from the very first migration so that the web-of-trust work can
    later tell a grandfathered account from a genuinely vouched one without a
    data archaeology exercise.
    """

    PRE_SSO_MIGRATION = "pre-sso-migration", _("Pre-SSO migration")
    INVITATION = "invitation", _("Invitation")


class TrustState(models.TextChoices):
    """Whether this account's trust still stands.

    Two ways of losing it, deliberately kept apart. ``REVOKED`` is what happens
    to the person somebody acted on: their realm roles are gone, their sessions
    ended and their realm account disabled. ``SUSPENDED`` is what happens to the
    people they vouched for, who have done nothing wrong - they can still sign
    in, and see why they can do nothing.
    """

    ACTIVE = "active", _("Active")
    REVOKED = "revoked", _("Revoked")
    SUSPENDED = "suspended", _("Suspended")


class KeycloakIdentity(models.Model):
    """The binding between a Django user and a Keycloak subject.

    ``sub`` is the only durable identifier Keycloak offers: usernames and email
    addresses both change, and matching on either of them would let a renamed
    or re-registered realm account take over somebody else's site account.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        # PROTECT, not CASCADE: an accidental user delete must not silently
        # discard the mapping that the audit trail is written against.
        on_delete=models.PROTECT,
        related_name="keycloak_identity",
        verbose_name=_("user"),
    )
    sub = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name=_("Keycloak subject"),
        help_text=_("The 'sub' claim. Immutable for the life of the account."),
    )
    issuer = models.URLField(
        verbose_name=_("issuer"),
        help_text=_("The realm that issued the subject. Checked on every login."),
    )
    preferred_username = models.CharField(
        max_length=255, blank=True, verbose_name=_("preferred username")
    )
    email_at_link = models.EmailField(blank=True, verbose_name=_("email at link time"))
    linked_at = models.DateTimeField(auto_now_add=True, verbose_name=_("linked at"))
    link_method = models.CharField(
        max_length=32, choices=LinkMethod.choices, verbose_name=_("link method")
    )
    last_seen_roles = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("last seen roles"),
        # Not authoritative for permissions - those are reconciled onto Django
        # groups and flags at login, and reading them back from here would be
        # reading a cache nothing keeps fresh. It *is* what the trust tier is
        # computed from, because Django's groups and flags cannot tell an
        # administrator from a web maintainer: both are superusers here.
        help_text=_(
            "Roles carried by the most recent token, and so the role set as of "
            "that sign-in. Permissions come from the Django groups; the "
            "invitation tier comes from here."
        ),
    )

    # --- the trust graph -------------------------------------------------
    sponsor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        # PROTECT: an account with descendants cannot be deleted out from under
        # them. Re-parent or revoke the subtree first.
        on_delete=models.PROTECT,
        related_name="vouched_for",
        verbose_name=_("sponsor"),
        help_text=_("Who vouched for this account. Null only for a root."),
    )
    is_root = models.BooleanField(
        default=False,
        verbose_name=_("root of a trust tree"),
        help_text=_("Vouched for by nobody. There may be several, and they are peers."),
    )

    # --- withdrawing trust ------------------------------------------------
    trust_state = models.CharField(
        max_length=16,
        choices=TrustState.choices,
        default=TrustState.ACTIVE,
        db_index=True,
        verbose_name=_("trust"),
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revocations_performed",
        verbose_name=_("revoked by"),
    )
    revocation_reason = models.TextField(blank=True, verbose_name=_("reason"))
    roles_at_revocation = models.JSONField(
        default=list,
        blank=True,
        # Removed from Keycloak on revocation, so without this a reversal has
        # nothing to put back.
        help_text=_("The client roles held when trust was withdrawn."),
    )
    revoked_directly = models.BooleanField(
        default=False,
        verbose_name=_("revoked directly"),
        help_text=_(
            "True for the account somebody acted on, false for one suspended "
            "because its sponsor was. US-5.2 keeps the two apart."
        ),
    )

    # --- migration bookkeeping (Phase 4) ---------------------------------
    # Kept on the identity rather than in a side table because every one of
    # them is a fact about this binding, and the migration commands need to be
    # resumable after an interruption.
    setup_email_sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("setup email last sent at")
    )
    setup_email_send_count = models.PositiveIntegerField(
        default=0, verbose_name=_("setup emails sent")
    )
    # When a link was handed over instead of emailed. The link itself is never
    # stored: it is a bearer credential for the account.
    setup_link_issued_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("setup link last issued at")
    )
    setup_link_issue_count = models.PositiveIntegerField(
        default=0, verbose_name=_("setup links issued")
    )
    first_sso_login_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("first SSO login at")
    )
    last_sso_login_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("last SSO login at")
    )
    local_password_disabled_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("local password disabled at")
    )

    class Meta:
        verbose_name = _("Keycloak identity")
        verbose_name_plural = _("Keycloak identities")
        ordering = ("-linked_at",)
        constraints = [
            # US-2.1 wants the single-sponsor rule in the database. A check
            # constraint cannot ask Keycloak who is an administrator, so
            # rootness is a flag it can see.
            #
            # Grandfathered accounts are exempt rather than parented onto an
            # invented sponsor: US-9.4 asks for them to stay "an explicit
            # untrusted-by-default cohort, distinguishable in every query from
            # genuinely vouched accounts", and link_method is what distinguishes
            # them. Nobody vouched for them, and the graph should not pretend
            # somebody did.
            models.CheckConstraint(
                condition=models.Q(sponsor__isnull=False)
                | models.Q(is_root=True)
                | models.Q(link_method=LinkMethod.PRE_SSO_MIGRATION),
                name="identity_has_a_sponsor_or_is_a_root",
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.sub}"

    @property
    def trusted(self):
        """Whether this account may hold any permission at all."""
        return self.trust_state == TrustState.ACTIVE

    @property
    def has_logged_in_via_sso(self):
        """True once this identity has completed at least one SSO login.

        Disabling a local password gates on this: provisioning an account in
        Keycloak proves nothing about whether the person can actually reach it,
        and taking their password away before they can is how people get
        locked out.
        """
        return self.first_sso_login_at is not None


class SsoAuditEvent(models.Model):
    """An append-only record of authentication and migration events.

    Written for the events the migration plan needs evidence of: the weekly
    local-login report, the linking decisions taken during the migration
    window, and refusals of realm users who have no account here.
    """

    class Action(models.TextChoices):
        LINKED = "linked", _("Account linked to a Keycloak identity")
        SSO_LOGIN = "sso-login", _("Signed in via Keycloak")
        LOCAL_LOGIN = "local-login", _("Signed in with a local password")
        REFUSED = "refused", _("Refused: no account for this subject")
        ROLES_MIRRORED = "roles-mirrored", _("Roles mirrored from Keycloak")
        PROVISIONED = "provisioned", _("Provisioned in Keycloak")
        SETUP_EMAIL_SENT = "setup-email-sent", _("Setup email sent")
        SETUP_LINK_ISSUED = "setup-link-issued", _(
            "Setup link issued to an administrator"
        )
        INVITED = "invited", _("Invited: account created for somebody new")
        REVOKED_TRUST = "revoked-trust", _("Trust withdrawn")
        SUSPENDED = "suspended", _("Suspended: their sponsor was revoked")
        RESTORED = "restored", _("Trust restored")
        LOCAL_PASSWORD_DISABLED = "local-password-disabled", _(
            "Local password made unusable"
        )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=32, choices=Action.choices, db_index=True)
    # SET_NULL so that deleting a user cannot take the audit trail with it.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sso_audit_events",
    )
    # Denormalised so the record still says who it was about after the user
    # row is gone.
    username = models.CharField(max_length=255, blank=True)
    sub = models.CharField(max_length=255, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = _("SSO audit event")
        verbose_name_plural = _("SSO audit events")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["action", "created_at"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.username}"

    @classmethod
    def record(cls, action, user=None, sub="", username="", **detail):
        """Write an event, never raising into the caller's control flow.

        Audit logging must not be able to fail a login: a broken write here
        would turn a diagnostic into an outage.
        """
        import logging

        try:
            return cls.objects.create(
                action=action,
                user=user,
                username=username or (getattr(user, "username", "") or ""),
                sub=sub or "",
                detail=detail,
            )
        except Exception:  # pragma: no cover - defensive
            logging.getLogger(__name__).exception(
                "Could not write SSO audit event %s", action
            )
            return None
