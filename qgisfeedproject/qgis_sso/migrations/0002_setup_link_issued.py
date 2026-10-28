# coding=utf-8
"""Bookkeeping for setup links handed over rather than emailed.

Purely additive: two nullable/defaulted columns and one widened choice list, so
the reverse migration drops the columns and narrows the list back. Rolling back
loses only the record of which links were issued; no identity or audit row is
removed, and any `setup-link-issued` events already written survive the choice
narrowing because Django does not validate choices at the database level.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgis_sso", "0001_initial"),
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
