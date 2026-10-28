# coding=utf-8
"""Somebody can offer to sponsor an account a cascade suspended.

One new table for the offer, plus four audit actions for its life: offered,
accepted, declined, withdrawn. The partial unique index keeps one open offer
per pair, so a double-clicked form cannot leave two rows waiting.

The reverse drops the table and the four actions. Offers already answered are
history rather than state, so nothing else depends on them and no data has to
move either way.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("qgis_sso", "0007_cancel_invitation"),
    ]

    operations = [
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
                        "sponsorship-offered",
                        "Offered to sponsor a suspended account",
                    ),
                    ("sponsorship-accepted", "Offer to sponsor accepted"),
                    ("sponsorship-declined", "Offer to sponsor declined"),
                    ("sponsorship-withdrawn", "Offer to sponsor withdrawn"),
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
        migrations.CreateModel(
            name="SponsorshipOffer",
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
                ("reason", models.TextField(verbose_name="reason")),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Waiting for an answer"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                            ("withdrawn", "Withdrawn"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                        verbose_name="state",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "responded_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="answered at"
                    ),
                ),
                (
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sponsorship_offers",
                        to="qgis_sso.keycloakidentity",
                        verbose_name="account offered a sponsor",
                    ),
                ),
                (
                    "sponsor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sponsorships_offered",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="would-be sponsor",
                    ),
                ),
            ],
            options={
                "verbose_name": "sponsorship offer",
                "verbose_name_plural": "sponsorship offers",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="sponsorshipoffer",
            constraint=models.UniqueConstraint(
                condition=models.Q(("state", "pending")),
                fields=("identity", "sponsor"),
                name="one_open_sponsorship_offer",
            ),
        ),
    ]
