# coding=utf-8
"""Cancelling an invitation removes the account, so nothing has to be marked.

Freeing a place kept the account and recorded that it no longer counted.
Cancelling deletes the account, the identity and the local user instead, so the
two columns have no job left and go. The audit action is renamed to match: the
event now says an invitation was cancelled, not that a place was given up.

The reverse puts the columns back, empty, and restores the old action name.
Rows already written as ``invitation-cancelled`` stay readable either way,
because choices are validated by forms rather than by the database.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("qgis_sso", "0006_invitation_released"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="keycloakidentity",
            name="invitation_released_at",
        ),
        migrations.RemoveField(
            model_name="keycloakidentity",
            name="invitation_released_by",
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
                    (
                        "invitation-cancelled",
                        "Invitation cancelled: the account it made was removed",
                    ),
                    ("local-password-disabled", "Local password made unusable"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
