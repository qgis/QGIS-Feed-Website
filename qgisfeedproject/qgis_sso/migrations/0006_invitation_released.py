# coding=utf-8
"""Give a sponsor a way to free the place an invitation is holding.

Two nullable columns and one more audit action. Null means the place is still
held, which is what every existing row means, so there is nothing to backfill.

The reverse drops the columns and the record of who freed what. It is safe to
run, but the reason a place was free is lost with it, so reverse this only
alongside the code that reads the columns.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("qgis_sso", "0005_reparented_audit_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="keycloakidentity",
            name="invitation_released_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="place freed at"
            ),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="invitation_released_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invitations_released",
                to=settings.AUTH_USER_MODEL,
                verbose_name="place freed by",
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
                    ("re-parented", "Moved to a different sponsor"),
                    ("invitation-released", "Place freed: an invitation given up"),
                    ("local-password-disabled", "Local password made unusable"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
