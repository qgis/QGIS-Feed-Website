# coding=utf-8
"""Make local passwords unusable for accounts that have proven SSO works.

Run late and deliberately. The gate is a *recorded successful SSO login*, not
merely having been provisioned: taking somebody's password away before they
have signed in through Keycloak once is how people get locked out of an
account they can no longer recover on their own.

Accounts that linked themselves through the Phase 3 migration-linking path
already had this done at link time, so they are skipped here.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from ...models import KeycloakIdentity, SsoAuditEvent


class Command(BaseCommand):
    help = (
        "Set an unusable password on every account that has a Keycloak "
        "identity and has signed in through it at least once. Dry run unless "
        "--commit is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually disable the passwords.",
        )
        parser.add_argument(
            "--username",
            action="append",
            default=[],
            help="Limit to these Django usernames. Repeatable.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]

        identities = KeycloakIdentity.objects.select_related("user").filter(
            first_sso_login_at__isnull=False,
            local_password_disabled_at__isnull=True,
        )
        if options["username"]:
            identities = identities.filter(user__username__in=options["username"])

        if not commit:
            self.stdout.write(
                self.style.WARNING("DRY RUN - no password will change. Add --commit.")
            )

        changed = already = 0
        for identity in identities:
            user = identity.user
            if not user.has_usable_password():
                # Already unusable, most likely from migration linking. Record
                # the fact so the report stops listing it.
                already += 1
                if commit:
                    identity.local_password_disabled_at = timezone.now()
                    identity.save(update_fields=["local_password_disabled_at"])
                continue

            self.stdout.write(
                f"{'DISABLE' if commit else 'would disable'} local password for "
                f"{user.username} (first SSO login "
                f"{identity.first_sso_login_at:%Y-%m-%d})"
            )
            if not commit:
                changed += 1
                continue

            user.set_unusable_password()
            user.save(update_fields=["password"])
            identity.local_password_disabled_at = timezone.now()
            identity.save(update_fields=["local_password_disabled_at"])
            SsoAuditEvent.record(
                SsoAuditEvent.Action.LOCAL_PASSWORD_DISABLED,
                user=user,
                sub=identity.sub,
            )
            changed += 1

        verb = "Disabled" if commit else "Would disable"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {changed}; {already} already had no usable password."
            )
        )

        remaining = KeycloakIdentity.objects.filter(
            first_sso_login_at__isnull=True
        ).count()
        if remaining:
            self.stdout.write(
                f"{remaining} provisioned account(s) have never signed in via "
                "SSO and were left alone on purpose."
            )
