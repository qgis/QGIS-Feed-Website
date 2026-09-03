# coding=utf-8
"""Initial identity and audit tables.

Purely additive: it creates two new tables and touches nothing that exists, so
the reverse migration is a clean drop of both. Rolling back before any account
has been linked loses nothing; rolling back afterwards discards the bindings
and the audit trail, so take a dump first.
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
                        help_text="Roles carried by the most recent token. Never authoritative.",
                        verbose_name="last seen roles",
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
                                "local-password-disabled",
                                "Local password made unusable",
                            ),
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
            },
        ),
        migrations.AddIndex(
            model_name="ssoauditevent",
            index=models.Index(
                fields=["action", "created_at"], name="qgis_sso_ss_action_528fa0_idx"
            ),
        ),
    ]
