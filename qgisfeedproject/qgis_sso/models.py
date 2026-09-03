# coding=utf-8
"""Identity and audit models for Keycloak single sign-on."""

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
        # Diagnostics only. Permissions are reconciled onto Django groups and
        # flags at login; reading them back from here would be reading a cache
        # that nothing keeps fresh.
        help_text=_("Roles carried by the most recent token. Never authoritative."),
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

    def __str__(self):
        return f"{self.user} → {self.sub}"

    @property
    def has_logged_in_via_sso(self):
        """True once this identity has completed at least one SSO login.

        ``sso_disable_local_passwords`` gates on this: provisioning an account
        in Keycloak proves nothing about whether the person can actually reach
        it, and taking their password away before they can is how people get
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
