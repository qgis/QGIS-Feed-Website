# coding=utf-8
"""Withdrawing trust: the state and the record of who withdrew it.

Additive. Every existing identity becomes ``active``, which is what it was
implicitly. The reverse drops the columns, which loses the record of any
revocation performed in the meantime - take a dump first if that matters.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgis_sso", "0003_sponsor"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="keycloakidentity",
            name="trust_state",
            field=models.CharField(
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
        migrations.AddField(
            model_name="keycloakidentity",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="revoked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="revocations_performed",
                to=settings.AUTH_USER_MODEL,
                verbose_name="revoked by",
            ),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="revocation_reason",
            field=models.TextField(blank=True, verbose_name="reason"),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="roles_at_revocation",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="The client roles held when trust was withdrawn.",
            ),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="revoked_directly",
            field=models.BooleanField(
                default=False,
                help_text="True for the account somebody acted on, false for "
                "one suspended because its sponsor was. US-5.2 keeps the two "
                "apart.",
                verbose_name="revoked directly",
            ),
        ),
        migrations.AlterField(
            model_name="ssoauditevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("linked", "Account linked to a Keycloak identity"),
                    ("sso-login", "Signed in via Keycloak"),
                    ("local-login", "Signed in with a local password"),
                    ("refused", "Refused: no account for this subject"),
                    ("roles-mirrored", "Roles mirrored from Keycloak"),
                    ("provisioned", "Provisioned in Keycloak"),
                    ("setup-email-sent", "Setup email sent"),
                    ("setup-link-issued", "Setup link issued to an administrator"),
                    ("invited", "Invited: account created for somebody new"),
                    ("revoked-trust", "Trust withdrawn"),
                    ("suspended", "Suspended: their sponsor was revoked"),
                    ("restored", "Trust restored"),
                    ("local-password-disabled", "Local password made unusable"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
