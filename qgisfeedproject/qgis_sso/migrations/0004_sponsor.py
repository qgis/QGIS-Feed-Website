# coding=utf-8
"""Give every identity a sponsor, or mark it as a root of the trust tree.

Every row written before this migration has ``link_method`` set to
``pre-sso-migration``, which the new constraint accepts, so it applies to
existing data as it stands. The reverse migration drops the sponsor column and
with it the record of who vouched for whom, so take a dump first.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("qgis_sso", "0003_setup_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="keycloakidentity",
            name="sponsor",
            field=models.ForeignKey(
                blank=True,
                help_text="Who vouched for this account. Null only for a root.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="vouched_for",
                to=settings.AUTH_USER_MODEL,
                verbose_name="sponsor",
            ),
        ),
        migrations.AddField(
            model_name="keycloakidentity",
            name="is_root",
            field=models.BooleanField(
                default=False,
                help_text="Vouched for by nobody. There may be several, and they are peers.",
                verbose_name="root of a trust tree",
            ),
        ),
        migrations.AlterField(
            model_name="keycloakidentity",
            name="link_method",
            field=models.CharField(
                choices=[
                    ("pre-sso-migration", "Pre-SSO migration"),
                    ("invitation", "Invitation"),
                ],
                max_length=32,
                verbose_name="link method",
            ),
        ),
        migrations.AlterField(
            model_name="keycloakidentity",
            name="last_seen_roles",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Roles carried by the most recent token, and so the role set as of that sign-in. Permissions come from the Django groups, and the invitation tier from these roles.",
                verbose_name="last seen roles",
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
        migrations.AddConstraint(
            model_name="keycloakidentity",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("sponsor__isnull", False),
                    ("is_root", True),
                    ("link_method", "pre-sso-migration"),
                    _connector="OR",
                ),
                name="identity_has_a_sponsor_or_is_a_root",
            ),
        ),
    ]
