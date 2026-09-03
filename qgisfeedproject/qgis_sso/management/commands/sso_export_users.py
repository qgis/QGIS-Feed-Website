# coding=utf-8
"""Report on the accounts a Keycloak migration would touch. Changes nothing.

Nothing else in Phase 4 should be run until a maintainer has read this. The
report is the artefact the migration is planned against, and the flagged rows
are the ones that cannot be migrated unattended.
"""

import csv
import json
import sys

from django.core.management.base import BaseCommand

from ...keycloak import KeycloakAdminClient, KeycloakError
from ...migration import flag_users, migratable_users, proposed_roles, proposed_username
from ...models import KeycloakIdentity

FIELDS = [
    "id",
    "username",
    "email",
    "is_active",
    "is_staff",
    "is_superuser",
    "groups",
    "last_login",
    "date_joined",
    "already_linked",
    "proposed_keycloak_username",
    "proposed_client_roles",
    "flags",
]


class Command(BaseCommand):
    help = (
        "Export every Django account with the Keycloak username and client roles "
        "the migration would give it, flagging the accounts that need a human "
        "decision first. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("csv", "json"),
            default="csv",
            help="Output format (default: csv).",
        )
        parser.add_argument(
            "--output",
            help="Write to this file instead of stdout.",
        )
        parser.add_argument(
            "--dormant-months",
            type=int,
            default=12,
            help=(
                "Accounts whose last login is older than this are flagged "
                "dormant (default: 12)."
            ),
        )
        parser.add_argument(
            "--check-realm",
            action="store_true",
            help=(
                "Also ask Keycloak whether each proposed username already "
                "exists. Requires the provisioner credentials. Slow, and the "
                "most important check in the report: a username that already "
                "exists may belong to somebody else."
            ),
        )

    def handle(self, *args, **options):
        users = list(migratable_users())
        flags = flag_users(users, dormant_months=options["dormant_months"])
        linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))

        client = None
        if options["check_realm"]:
            try:
                client = KeycloakAdminClient()
            except KeycloakError as error:
                raise SystemExit(f"--check-realm is not usable: {error}")

        rows = []
        for user in users:
            username = proposed_username(user)
            row_flags = list(flags.get(user.pk, []))

            if client is not None:
                try:
                    if client.find_user_by_username(username) is not None:
                        row_flags.append("username-exists-in-realm")
                except KeycloakError as error:
                    self.stderr.write(f"Realm lookup failed for {username}: {error}")
                    row_flags.append("realm-lookup-failed")

            rows.append(
                {
                    "id": user.pk,
                    "username": user.username,
                    "email": user.email,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                    "groups": "|".join(
                        sorted(group.name for group in user.groups.all())
                    ),
                    "last_login": (
                        user.last_login.isoformat() if user.last_login else ""
                    ),
                    "date_joined": (
                        user.date_joined.isoformat() if user.date_joined else ""
                    ),
                    "already_linked": user.pk in linked,
                    "proposed_keycloak_username": username,
                    "proposed_client_roles": "|".join(proposed_roles(user)),
                    "flags": "|".join(row_flags),
                }
            )

        self._write(rows, options)
        self._summarise(rows)

    def _write(self, rows, options):
        if options["output"]:
            handle = open(options["output"], "w", newline="", encoding="utf-8")
        else:
            handle = sys.stdout
        try:
            if options["format"] == "json":
                json.dump(rows, handle, indent=2, sort_keys=True)
                handle.write("\n")
            else:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        finally:
            if handle is not sys.stdout:
                handle.close()
                self.stdout.write(f"Wrote {len(rows)} rows to {options['output']}")

    def _summarise(self, rows):
        """Counts to stderr, so they survive a redirect of the report itself."""
        counts = {}
        for row in rows:
            for flag in filter(None, row["flags"].split("|")):
                counts[flag] = counts.get(flag, 0) + 1

        self.stderr.write(f"\n{len(rows)} accounts examined.")
        if not counts:
            self.stderr.write("No accounts flagged.")
            return
        self.stderr.write("Flagged accounts, by reason:")
        for flag, count in sorted(counts.items(), key=lambda item: -item[1]):
            self.stderr.write(f"  {count:6d}  {flag}")
        self.stderr.write(
            "\nEvery flagged account needs a decision before "
            "sso_provision_keycloak runs. 'username-exists-in-realm' is the "
            "dangerous one: linking to a pre-existing realm identity may hand "
            "site access to a different person."
        )
