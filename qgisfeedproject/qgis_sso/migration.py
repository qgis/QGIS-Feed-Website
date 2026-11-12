# coding=utf-8
"""What a Django account would become in Keycloak.

The export report and provisioning share these rules, so the report shows
exactly what provisioning will do.
"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import validation

#: Roles on the feed client, most privileged first.
ROLE_ADMIN = "admin"
ROLE_WEB_MAINTAINER = "web-maintainer"
ROLE_REVIEWER = "reviewer"
ROLE_USERGROUP_AUTHOR = "usergroup-author"
ROLE_AUTHOR = "author"

APPROVER_GROUP = "qgisfeedentry_approver"
AUTHORS_GROUP = "qgisfeedentry_authors"

#: Passkey only. No password is ever set, so enrolment needs a device that can
#: create a passkey.
DEFAULT_REQUIRED_ACTIONS = ["VERIFY_EMAIL", "webauthn-register-passwordless"]


def required_actions():
    return list(getattr(settings, "SSO_REQUIRED_ACTIONS", DEFAULT_REQUIRED_ACTIONS))


def feed_client_id():
    """The Keycloak client whose roles this site reads and signs in with."""
    return settings.OIDC_RP_CLIENT_ID


def proposed_username(user):
    """The Keycloak username this Django account should get.

    Keycloak lowercases usernames and Django does not, so two accounts here can
    want one realm account. The invite form refuses a name taken here in any
    case, and provisioning never claims a realm account it did not create.
    """
    return validation.clean_username(user.username)


def proposed_roles(user, can_publish=None):
    """Client roles implied by what the account can already do here.

    The migration therefore grants nothing new. Pass ``can_publish`` when
    listing many accounts, because ``user.has_perm`` costs two queries a user.
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
    """The warnings in the export report, as ``{user_id: [flag, ...]}``.

    A flag means a person has to decide about the account. Provisioning skips
    flagged accounts unless told otherwise. This asks the database nothing.
    """
    users = list(users)
    cutoff = dormant_cutoff(dormant_months)
    flags = defaultdict(list)

    for user in users:
        if not user.email:
            # The setup link is emailed. No address means no migration path.
            flags[user.pk].append("no-email")

        if user.last_login is None:
            flags[user.pk].append("never-logged-in")
        elif user.last_login < cutoff:
            flags[user.pk].append("dormant")

        if not user.is_active:
            flags[user.pk].append("inactive")

    return flags


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
