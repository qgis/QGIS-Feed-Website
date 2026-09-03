# coding=utf-8
"""Create realm accounts for existing Django users and record their subjects.

Creates users only. It sends no email: that is ``sso_send_setup_links``, and
the two are separate so that provisioning the whole population cannot
accidentally email the whole population.
"""

from django.core.management.base import BaseCommand, CommandError

from ...keycloak import KeycloakAdminClient, KeycloakError
from ...migration import (
    feed_client_id,
    flag_users,
    migratable_users,
    proposed_roles,
    proposed_username,
    required_actions,
)
from ...models import KeycloakIdentity, LinkMethod, SsoAuditEvent


class Command(BaseCommand):
    help = (
        "Create Keycloak accounts for Django users that do not have one, and "
        "record the returned subject locally. Dry run unless --commit is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually create accounts. Without it nothing is written.",
        )
        parser.add_argument(
            "--username",
            action="append",
            default=[],
            help="Limit to these Django usernames. Repeatable.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Stop after this many accounts. Use it to run in small waves.",
        )
        parser.add_argument(
            "--include-flagged",
            action="store_true",
            help=(
                "Provision accounts flagged by sso_export_users too. Only after "
                "a maintainer has read the report and decided about each one."
            ),
        )
        parser.add_argument(
            "--dormant-months",
            type=int,
            default=12,
            help="Dormancy threshold used for flagging (default: 12).",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        users = list(migratable_users())
        if options["username"]:
            wanted = set(options["username"])
            users = [user for user in users if user.username in wanted]
            missing = wanted - {user.username for user in users}
            if missing:
                raise CommandError(f"No such user(s): {', '.join(sorted(missing))}")

        flags = flag_users(users, dormant_months=options["dormant_months"])
        linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))

        try:
            client = KeycloakAdminClient()
            client_uuid = client.client_uuid(feed_client_id())
            available_roles = client.client_roles(client_uuid)
        except KeycloakError as error:
            raise CommandError(str(error))

        if not commit:
            self.stdout.write(
                self.style.WARNING("DRY RUN - nothing will be created. Add --commit.")
            )

        created = skipped = failed = 0
        for user in users:
            if options["limit"] is not None and created >= options["limit"]:
                self.stdout.write(f"Reached --limit {options['limit']}; stopping.")
                break

            username = proposed_username(user)

            if user.pk in linked:
                self._skip(user, "already has a Keycloak identity")
                skipped += 1
                continue
            if not user.email:
                # Provisioning without an address produces an account nobody
                # can ever reach, because the setup link is emailed.
                self._skip(user, "no email address")
                skipped += 1
                continue

            user_flags = flags.get(user.pk, [])
            if user_flags and not options["include_flagged"]:
                self._skip(user, f"flagged ({', '.join(user_flags)})")
                skipped += 1
                continue

            try:
                existing = client.find_user_by_username(username)
            except KeycloakError as error:
                self.stderr.write(
                    self.style.ERROR(f"{username}: lookup failed: {error}")
                )
                failed += 1
                continue

            if existing is not None:
                # Never claim a realm account we did not create. It may belong
                # to somebody else entirely.
                self._skip(
                    user,
                    f"realm already has a user named {username!r}; not claiming it",
                )
                skipped += 1
                continue

            roles = proposed_roles(user)
            unknown = [role for role in roles if role not in available_roles]
            if unknown:
                self.stderr.write(
                    self.style.ERROR(
                        f"{username}: client {feed_client_id()} has no role(s) "
                        f"{', '.join(unknown)}. Deploy the realm changes first."
                    )
                )
                failed += 1
                continue

            self.stdout.write(
                f"{'CREATE' if commit else 'would create'} {username} "
                f"<{user.email}> roles={roles or ['(none)']}"
            )
            if not commit:
                created += 1
                continue

            try:
                sub = client.create_user(
                    {
                        "username": username,
                        "email": user.email,
                        "firstName": user.first_name,
                        "lastName": user.last_name,
                        "enabled": user.is_active,
                        # Django never verified these addresses, and the whole
                        # migration hangs off email reaching the right person.
                        "emailVerified": False,
                        # No credentials: the user sets their own through the
                        # setup link, so no password we chose ever exists.
                        "requiredActions": required_actions(),
                    }
                )
                if roles:
                    client.assign_client_roles(
                        sub, client_uuid, [available_roles[role] for role in roles]
                    )
                KeycloakIdentity.objects.create(
                    user=user,
                    sub=sub,
                    issuer=f"{client.server_url}/realms/{client.realm}",
                    preferred_username=username,
                    email_at_link=user.email,
                    link_method=LinkMethod.PRE_SSO_MIGRATION,
                )
                SsoAuditEvent.record(
                    SsoAuditEvent.Action.PROVISIONED,
                    user=user,
                    sub=sub,
                    keycloak_username=username,
                    roles=roles,
                )
            except KeycloakError as error:
                self.stderr.write(self.style.ERROR(f"{username}: {error}"))
                failed += 1
                continue

            created += 1

        verb = "Created" if commit else "Would create"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {created}; skipped {skipped}; failed {failed}.")
        )
        if failed:
            raise CommandError(
                f"{failed} account(s) failed. The command is resumable: fix the "
                "cause and run it again, already-provisioned accounts are skipped."
            )

    def _skip(self, user, reason):
        self.stdout.write(f"skip {user.username}: {reason}")
