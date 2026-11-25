# coding=utf-8
"""Record when an identity was sent its setup email, and how often.

Additive, so the reverse migration drops the two columns and the counts with
them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgis_sso", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="keycloakidentity",
            name="setup_email_sent_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="setup email last sent at"
            ),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="setup_email_send_count",
            field=models.PositiveIntegerField(
                default=0, verbose_name="setup emails sent"
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
                    ("local-password-disabled", "Local password made unusable"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
