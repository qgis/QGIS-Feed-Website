# coding=utf-8
"""The sponsor link that makes the trust graph a graph.

Additive: two columns and one constraint. The reverse drops them, losing the
record of who vouched for whom but touching no identity and no audit row.

No backfill. Existing accounts predate invitations and nobody vouched for them,
so they are left sponsorless and the constraint exempts them by link method.
US-9.4 asks for exactly that - grandfathered accounts stay "an explicit
untrusted-by-default cohort, distinguishable in every query from genuinely
vouched accounts" - and inventing a sponsor would make the graph say something
untrue that no later query could unpick.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgis_sso", "0002_setup_link_issued"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="keycloakidentity",
            name="is_root",
            field=models.BooleanField(
                default=False,
                help_text="Vouched for by nobody. There may be several, and "
                "they are peers.",
                verbose_name="root of a trust tree",
            ),
        ),
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
        migrations.AlterField(
            model_name="keycloakidentity",
            name="last_seen_roles",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Roles carried by the most recent token, and so the "
                "role set as of that sign-in. Permissions come from the Django "
                "groups; the invitation tier comes from here.",
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
                    ("local-password-disabled", "Local password made unusable"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="keycloakidentity",
            constraint=models.CheckConstraint(
                condition=models.Q(("sponsor__isnull", False))
                | models.Q(("is_root", True))
                | models.Q(("link_method", "pre-sso-migration")),
                name="identity_has_a_sponsor_or_is_a_root",
            ),
        ),
    ]
