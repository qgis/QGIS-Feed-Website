# coding=utf-8
"""Tables for Keycloak identities, invitations and the SSO audit trail.

Purely additive: it creates new tables and touches nothing that exists, so
the reverse migration drops them. Rolling back before any account has been
linked loses nothing. Rolling back afterwards discards the bindings and the
audit trail, so take a dump first.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KeycloakIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "sub",
                    models.CharField(
                        db_index=True,
                        help_text="The 'sub' claim. Immutable for the life of the account.",
                        max_length=255,
                        unique=True,
                        verbose_name="Keycloak subject",
                    ),
                ),
                (
                    "issuer",
                    models.URLField(
                        help_text="The realm that issued the subject. Checked on every login.",
                        verbose_name="issuer",
                    ),
                ),
                (
                    "preferred_username",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="preferred username"
                    ),
                ),
                (
                    "email_at_link",
                    models.EmailField(
                        blank=True, max_length=254, verbose_name="email at link time"
                    ),
                ),
                (
                    "linked_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="linked at"),
                ),
                (
                    "link_method",
                    models.CharField(
                        choices=[
                            ("pre-sso-migration", "Pre-SSO migration"),
                            ("invitation", "Invitation"),
                        ],
                        max_length=32,
                        verbose_name="link method",
                    ),
                ),
                (
                    "last_seen_roles",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Roles carried by the most recent token, and so the role set as of that sign-in. Permissions come from the Django groups; the invitation tier comes from here.",
                        verbose_name="last seen roles",
                    ),
                ),
                (
                    "is_root",
                    models.BooleanField(
                        default=False,
                        help_text="Vouched for by nobody. There may be several, and they are peers.",
                        verbose_name="root of a trust tree",
                    ),
                ),
                (
                    "trust_state",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("revoked", "Revoked"),
                            ("suspended", "Suspended"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=16,
                        verbose_name="trust",
                    ),
                ),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "revocation_reason",
                    models.TextField(blank=True, verbose_name="reason"),
                ),
                (
                    "roles_at_revocation",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="The client roles held when trust was withdrawn.",
                    ),
                ),
                (
                    "revoked_directly",
                    models.BooleanField(
                        default=False,
                        help_text="True for the account somebody acted on, false for one suspended because its sponsor was. US-5.2 keeps the two apart.",
                        verbose_name="revoked directly",
                    ),
                ),
                (
                    "invitation_released_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="place freed at"
                    ),
                ),
                (
                    "setup_email_sent_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="setup email last sent at"
                    ),
                ),
                (
                    "setup_email_send_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="setup emails sent"
                    ),
                ),
                (
                    "setup_link_issued_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="setup link last issued at"
                    ),
                ),
                (
                    "setup_link_issue_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="setup links issued"
                    ),
                ),
                (
                    "first_sso_login_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="first SSO login at"
                    ),
                ),
                (
                    "last_sso_login_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last SSO login at"
                    ),
                ),
                (
                    "local_password_disabled_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="local password disabled at"
                    ),
                ),
                (
                    "invitation_released_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="invitations_released",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="place freed by",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="revocations_performed",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="revoked by",
                    ),
                ),
                (
                    "sponsor",
                    models.ForeignKey(
                        blank=True,
                        help_text="Who vouched for this account. Null only for a root.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="vouched_for",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="sponsor",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="keycloak_identity",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="user",
                    ),
                ),
            ],
            options={
                "verbose_name": "Keycloak identity",
                "verbose_name_plural": "Keycloak identities",
                "ordering": ("-linked_at",),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("sponsor__isnull", False),
                            ("is_root", True),
                            ("link_method", "pre-sso-migration"),
                            _connector="OR",
                        ),
                        name="identity_has_a_sponsor_or_is_a_root",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="SsoAuditEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("linked", "Account linked to a Keycloak identity"),
                            ("sso-login", "Signed in via Keycloak"),
                            ("local-login", "Signed in with a local password"),
                            ("refused", "Refused: no account for this subject"),
                            ("roles-mirrored", "Roles mirrored from Keycloak"),
                            ("provisioned", "Provisioned in Keycloak"),
                            ("setup-email-sent", "Setup email sent"),
                            (
                                "setup-link-issued",
                                "Setup link issued to an administrator",
                            ),
                            ("invited", "Invited: account created for somebody new"),
                            ("revoked-trust", "Trust withdrawn"),
                            ("suspended", "Suspended: their sponsor was revoked"),
                            ("restored", "Trust restored"),
                            ("re-parented", "Moved to a different sponsor"),
                            (
                                "invitation-released",
                                "Place freed: an invitation given up",
                            ),
                            ("local-password-disabled", "Local password made unusable"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("username", models.CharField(blank=True, max_length=255)),
                ("sub", models.CharField(blank=True, max_length=255)),
                ("detail", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sso_audit_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "SSO audit event",
                "verbose_name_plural": "SSO audit events",
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["action", "created_at"],
                        name="qgis_sso_ss_action_528fa0_idx",
                    )
                ],
            },
        ),
    ]
