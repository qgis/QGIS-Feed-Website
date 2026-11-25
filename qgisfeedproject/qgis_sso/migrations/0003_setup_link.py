# coding=utf-8
"""Record when an identity's setup link was handed over, and how often.

Additive, so the reverse migration drops the two columns and the counts with
them. The link itself is never stored.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgis_sso", "0002_setup_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="keycloakidentity",
            name="setup_link_issued_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="setup link last issued at"
            ),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="setup_link_issue_count",
            field=models.PositiveIntegerField(
                default=0, verbose_name="setup links issued"
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
                    ("local-password-disabled", "Local password made unusable"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
