# coding=utf-8
"""What a Django account would become in Keycloak.

The mapping decisions - what Keycloak username a Django user gets, which
client roles their existing flags imply, and which accounts cannot be migrated
unattended - are defined once here, so that the exported report describes
exactly what provisioning will then do.
"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

#: Roles on the feed client, most privileged first.
ROLE_ADMIN = "admin"
ROLE_WEB_MAINTAINER = "web-maintainer"
ROLE_REVIEWER = "reviewer"
ROLE_USERGROUP_AUTHOR = "usergroup-author"
ROLE_AUTHOR = "author"

APPROVER_GROUP = "qgisfeedentry_approver"
AUTHORS_GROUP = "qgisfeedentry_authors"

#: Actions Keycloak makes the user complete before their account is usable.
#: A passkey and nothing else: no password is ever set, so there is no
#: password to phish, reuse or rotate, and no separate one-time code because a
#: passkey already combines something you have with something you are.
#: The cost is that enrolment needs a device that can create one - see the
#: recovery note in docs/sso.md.
DEFAULT_REQUIRED_ACTIONS = ["VERIFY_EMAIL", "webauthn-register-passwordless"]


def required_actions():
    return list(getattr(settings, "SSO_REQUIRED_ACTIONS", DEFAULT_REQUIRED_ACTIONS))


def feed_client_id():
    """The Keycloak client whose roles this site reads.

    The same client id the login flow authenticates against, so that roles are
    granted on the client they will later be read from.
    """
    return settings.OIDC_RP_CLIENT_ID


def proposed_username(user):
    """The Keycloak username this Django account should get.

    Keycloak lowercases usernames and enforces uniqueness; Django does not
    lowercase. Two Django accounts differing only in case therefore collide,
    which is why every export flags the collisions for a human before any
    account is created.
    """
    return user.username.strip().lower()


def proposed_roles(user, can_publish=None):
    """Client roles implied by the account's existing Django permissions.

    Derived from what the account can already do rather than invented, so the
    migration grants no privilege that was not already held.

    ``can_publish`` lets a caller listing many accounts resolve the permission
    for all of them in one query. ``user.has_perm`` cannot be prefetched - it
    issues two queries per user however the queryset was built - which is two
    hundred queries for a page of a hundred people.
    """
    roles = []
    group_names = {group.name for group in user.groups.all()}
    if can_publish is None:
        can_publish = user.has_perm("qgisfeed.publish_qgisfeedentry")

    if user.is_superuser:
        roles.append(ROLE_ADMIN)
    elif group_names & {APPROVER_GROUP} or can_publish:
        roles.append(ROLE_REVIEWER)
    elif user.is_staff or AUTHORS_GROUP in group_names:
        roles.append(ROLE_AUTHOR)

    return roles


def dormant_cutoff(months):
    """The datetime before which a last login counts as dormant."""
    return timezone.now() - timedelta(days=int(months) * 30)


def flag_users(users, dormant_months=12):
    """Compute the per-user warnings the export report is built from.

    Returns ``{user_id: [flag, ...]}``. Every flag means "a human has to
    decide about this account"; the migration commands skip flagged accounts
    unless told otherwise.
    """
    users = list(users)

    by_lowercase = defaultdict(list)
    by_email = defaultdict(list)
    for user in users:
        by_lowercase[proposed_username(user)].append(user)
        if user.email:
            by_email[user.email.strip().lower()].append(user)

    cutoff = dormant_cutoff(dormant_months)
    flags = defaultdict(list)

    for user in users:
        if not user.email:
            # The setup link is emailed. No address means no migration path.
            flags[user.pk].append("no-email")
        elif len(by_email[user.email.strip().lower()]) > 1:
            flags[user.pk].append("duplicate-email")

        if len(by_lowercase[proposed_username(user)]) > 1:
            flags[user.pk].append("username-case-collision")

        if user.last_login is None:
            flags[user.pk].append("never-logged-in")
        elif user.last_login < cutoff:
            flags[user.pk].append("dormant")

        if not user.is_active:
            flags[user.pk].append("inactive")

    return flags


def migratable_users():
    """Users the migration considers at all, with related data prefetched."""
    return User.objects.all().prefetch_related("groups").order_by("username")


#: Columns of the migration report, in order.
REPORT_FIELDS = [
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


def report_row(user, flags=(), linked_ids=()):
    """One row of the migration report: what provisioning would do, and why not."""
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "groups": "|".join(sorted(group.name for group in user.groups.all())),
        "last_login": user.last_login.isoformat() if user.last_login else "",
        "date_joined": user.date_joined.isoformat() if user.date_joined else "",
        "already_linked": user.pk in linked_ids,
        "proposed_keycloak_username": proposed_username(user),
        "proposed_client_roles": "|".join(proposed_roles(user)),
        "flags": "|".join(flags),
    }
