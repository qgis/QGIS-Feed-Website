# coding=utf-8
"""Report local-password sign-ins over a window.

The decision to retire local login is gated on this count reaching zero for
accounts active in the last year, so it has to be measured rather than
assumed. Intended to run weekly.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Count, Max
from django.utils import timezone

from ...models import KeycloakIdentity, SsoAuditEvent


class Command(BaseCommand):
    help = "Summarise local-password sign-ins and SSO migration progress."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Window to report on, in days (default: 7).",
        )
        parser.add_argument(
            "--active-months",
            type=int,
            default=12,
            help=(
                "An account counts towards the exit condition if it has logged "
                "in within this many months (default: 12)."
            ),
        )

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])

        rows = (
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.LOCAL_LOGIN, created_at__gte=since
            )
            .values("username")
            .annotate(count=Count("id"), last=Max("created_at"))
            .order_by("-count")
        )

        self.stdout.write(
            f"Local sign-ins in the last {options['days']} day(s): "
            f"{sum(row['count'] for row in rows)} across {len(rows)} account(s)."
        )
        for row in rows:
            self.stdout.write(
                f"  {row['count']:4d}  {row['username']:<32} "
                f"last {row['last']:%Y-%m-%d %H:%M}"
            )

        # The exit condition: every account active in the window holds a
        # linked Keycloak subject.
        cutoff = timezone.now() - timedelta(days=options["active_months"] * 30)
        active = User.objects.filter(is_active=True, last_login__gte=cutoff)
        linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))
        unlinked = [user for user in active if user.pk not in linked]

        self.stdout.write("")
        self.stdout.write(
            f"Exit condition: {active.count() - len(unlinked)}/{active.count()} "
            f"accounts active in the last {options['active_months']} months are "
            "linked to a Keycloak identity."
        )
        if unlinked:
            self.stdout.write("Not yet linked:")
            for user in unlinked[:50]:
                self.stdout.write(f"  {user.username} <{user.email}>")
            if len(unlinked) > 50:
                self.stdout.write(f"  ... and {len(unlinked) - 50} more")
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Condition (a) met. Local login can be retired once "
                    "break-glass access (US-1.3) exists and has been tested."
                )
            )
