# coding=utf-8
"""Email provisioned users a Keycloak account-setup link.

This is the command that sends real mail to real contributors, and the one
place in the migration where a mistake is not recoverable - you cannot unsend
a setup link that points at a throwaway staging realm.

Two guards, both fail-closed:

* ``SSO_SETUP_EMAIL_ALLOWLIST`` must be non-empty and must match the
  recipient. An unset allowlist sends nothing at all rather than everything.
  In production this is what makes "wave 1 is administrators only" a property
  of the code rather than of remembering the right ``--limit``.
* ``--commit`` is required. Without it the command reports what it would send.
"""

import fnmatch

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...keycloak import KeycloakAdminClient, KeycloakError
from ...migration import feed_client_id, required_actions
from ...models import KeycloakIdentity, SsoAuditEvent

#: 14 days. Keycloak's default is 12 hours, which is far too short for a
#: migration wave - people are on holiday.
DEFAULT_LIFESPAN_SECONDS = 1209600


def allowlist():
    return [
        pattern.strip().lower()
        for pattern in getattr(settings, "SSO_SETUP_EMAIL_ALLOWLIST", [])
        if pattern.strip()
    ]


def is_allowed(email, patterns):
    """Match an address against the allowlist.

    Patterns are shell globs, so a team can be allowed with
    ``*@kartoza.com`` without listing every address. A pattern of ``*`` is
    accepted but should only ever appear in a production settings file that a
    human has deliberately edited for a full wave.
    """
    address = (email or "").strip().lower()
    if not address:
        return False
    return any(fnmatch.fnmatch(address, pattern) for pattern in patterns)


class Command(BaseCommand):
    help = (
        "Send Keycloak account-setup emails to provisioned users. Refuses any "
        "recipient not matching SSO_SETUP_EMAIL_ALLOWLIST, and refuses "
        "everything if that setting is empty."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually send. Without it, nothing leaves the building.",
        )
        parser.add_argument(
            "--limit", type=int, help="Send at most this many emails in this run."
        )
        parser.add_argument(
            "--username",
            action="append",
            default=[],
            help="Limit to these Django usernames. Repeatable.",
        )
        parser.add_argument(
            "--resend",
            action="store_true",
            help="Include users who have already been sent a link.",
        )
        parser.add_argument(
            "--since",
            help=(
                "Only users linked on or after this ISO date, so a later wave "
                "does not re-cover an earlier one."
            ),
        )
        parser.add_argument(
            "--lifespan",
            type=int,
            default=getattr(
                settings, "SSO_SETUP_LINK_LIFESPAN", DEFAULT_LIFESPAN_SECONDS
            ),
            help="Link validity in seconds (default: 14 days).",
        )

    def handle(self, *args, **options):
        patterns = allowlist()
        if not patterns:
            raise CommandError(
                "SSO_SETUP_EMAIL_ALLOWLIST is empty, so no recipient is "
                "permitted and nothing has been sent. This is the guard "
                "working: set it in settings_local to the addresses this wave "
                "is meant to reach. Note that a staging database restored from "
                "production contains every contributor's real address."
            )

        identities = KeycloakIdentity.objects.select_related("user").order_by(
            "linked_at"
        )
        if options["username"]:
            identities = identities.filter(user__username__in=options["username"])
        if options["since"]:
            identities = identities.filter(linked_at__gte=options["since"])
        if not options["resend"]:
            identities = identities.filter(setup_email_sent_at__isnull=True)

        commit = options["commit"]
        if not commit:
            self.stdout.write(
                self.style.WARNING("DRY RUN - no email will be sent. Add --commit.")
            )

        self.stdout.write(f"Allowlist: {', '.join(patterns)}")

        try:
            client = KeycloakAdminClient()
        except KeycloakError as error:
            raise CommandError(str(error))

        redirect_uri = getattr(settings, "SSO_SETUP_REDIRECT_URI", "")
        if not redirect_uri:
            raise CommandError(
                "SSO_SETUP_REDIRECT_URI is not set. It must point at this "
                "site, and it must be a registered redirect URI on the "
                f"{feed_client_id()} client or Keycloak will reject the link."
            )

        sent = refused = failed = 0
        for identity in identities:
            if options["limit"] is not None and sent >= options["limit"]:
                self.stdout.write(f"Reached --limit {options['limit']}; stopping.")
                break

            email = identity.user.email
            if not is_allowed(email, patterns):
                self.stdout.write(
                    f"refuse {identity.user.username} <{email}>: not on the allowlist"
                )
                refused += 1
                continue

            self.stdout.write(
                f"{'SEND' if commit else 'would send'} to "
                f"{identity.user.username} <{email}>"
            )
            if not commit:
                sent += 1
                continue

            try:
                client.execute_actions_email(
                    identity.sub,
                    required_actions(),
                    client_id=feed_client_id(),
                    redirect_uri=redirect_uri,
                    lifespan=options["lifespan"],
                )
            except KeycloakError as error:
                self.stderr.write(
                    self.style.ERROR(f"{identity.user.username}: {error}")
                )
                failed += 1
                continue

            # Recorded per user so the run is resumable and --resend is a
            # deliberate act rather than the default.
            identity.setup_email_sent_at = timezone.now()
            identity.setup_email_send_count += 1
            identity.save(
                update_fields=["setup_email_sent_at", "setup_email_send_count"]
            )
            SsoAuditEvent.record(
                SsoAuditEvent.Action.SETUP_EMAIL_SENT,
                user=identity.user,
                sub=identity.sub,
                lifespan=options["lifespan"],
                send_count=identity.setup_email_send_count,
            )
            sent += 1

        verb = "Sent" if commit else "Would send"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {sent}; refused by allowlist {refused}; failed {failed}."
            )
        )
