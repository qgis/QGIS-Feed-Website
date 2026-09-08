# coding=utf-8
"""Where each account has got to in the migration to single sign-on.

Read entirely from the local database. Asking Keycloak would be more
authoritative but would cost one round trip per row before the page could be
shown, so the realm is only contacted when somebody actually asks for
something to happen.

The states are ordered: an account moves down the table and never back up
without a deliberate act, which is what makes them usable as a filter.
"""

from dataclasses import dataclass, field

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .migration import flag_users, proposed_roles, proposed_username
from .models import KeycloakIdentity

#: The permission that makes somebody a reviewer in Keycloak. Matched on the
#: app label too, so a same-named permission in another app cannot grant it.
PUBLISH_APP = "qgisfeed"
PUBLISH_PERMISSION = "publish_qgisfeedentry"

BLOCKED = "blocked"
NOT_PROVISIONED = "not-provisioned"
PROVISIONED = "provisioned"
INVITED = "invited"
ENROLLED = "enrolled"
MIGRATED = "migrated"

#: Label and Bulma modifier per state. The label carries the meaning; the
#: colour only repeats it, so the table stays readable without it.
STATES = {
    BLOCKED: (_("Needs a decision"), "is-danger"),
    NOT_PROVISIONED: (_("No realm account"), "is-light"),
    PROVISIONED: (_("Account created"), "is-info"),
    INVITED: (_("Invited"), "is-warning"),
    ENROLLED: (_("Signed in"), "is-success"),
    MIGRATED: (_("Migrated"), "is-success"),
}

#: The states an account with a realm identity can be in, in the order it
#: passes through them, which is also the order the filter offers. ``BLOCKED``
#: and ``NOT_PROVISIONED`` describe accounts that have no identity yet; those
#: live on the create page, which does not filter by state.
LINKED_STATES = [PROVISIONED, INVITED, ENROLLED, MIGRATED]

#: Flags that put an account beyond enrolment altogether. Without an address
#: there is nowhere to send the invitation, and a deactivated account is one
#: somebody deliberately closed. Both belong in the admin user list, not on a
#: page about moving people forward.
#:
#: The other flags stay: dormant and never-logged-in accounts can be enrolled,
#: and a duplicate email or a username case collision is something a human can
#: resolve and then come back to.
HIDDEN_FLAGS = frozenset({"no-email", "inactive"})


@dataclass
class Row:
    """One account as the enrolment page sees it."""

    user: object
    identity: object = None
    state: str = NOT_PROVISIONED
    flags: list = field(default_factory=list)
    proposed_username: str = ""
    proposed_roles: list = field(default_factory=list)

    @property
    def label(self):
        return STATES[self.state][0]

    @property
    def css_class(self):
        return STATES[self.state][1]

    @property
    def enrollable(self):
        """False for an account no invitation could ever reach."""
        return not HIDDEN_FLAGS.intersection(self.flags)


def state_of(identity, flags):
    """Which state one account is in.

    A flag only blocks an account that has not been provisioned yet. Once
    somebody has looked at it and gone ahead, the useful thing to show is how
    far it has got; the flags stay beside it either way.
    """
    if identity is None:
        return BLOCKED if flags else NOT_PROVISIONED
    if identity.local_password_disabled_at is not None:
        return MIGRATED
    if identity.first_sso_login_at is not None:
        return ENROLLED
    if identity.setup_email_sent_at or identity.setup_link_issued_at:
        return INVITED
    return PROVISIONED


def rows(users, flags=None):
    """Build one :class:`Row` per user, without an N+1.

    ``flags`` is computed against the whole population when not supplied: a
    username case collision is only visible when the other account is in view
    too, so it cannot be derived from a single page of results.
    """
    users = list(users)
    if flags is None:
        flags = flag_users(all_candidates())

    # Every identity in one query rather than an IN clause over the whole user
    # table: there is at most one per user and usually far fewer.
    identities = {
        identity.user_id: identity for identity in KeycloakIdentity.objects.all()
    }
    can_publish = publishers()

    built = []
    for user in users:
        identity = identities.get(user.pk)
        user_flags = flags.get(user.pk, [])
        built.append(
            Row(
                user=user,
                identity=identity,
                state=state_of(identity, user_flags),
                flags=user_flags,
                proposed_username=proposed_username(user),
                proposed_roles=proposed_roles(user, user.pk in can_publish),
            )
        )
    return built


def linked_rows():
    """Accounts that exist in the realm, for the enrolment list.

    These are the ones with somewhere still to go: created, invited, signed in,
    migrated. Ordered by username so paging is stable.
    """
    linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))
    population = list(all_candidates().order_by("username"))
    flags = flag_users(population)
    return [row for row in rows(population, flags=flags) if row.user.pk in linked]


def candidate_rows():
    """Accounts with no realm identity, split into the ones worth offering.

    Returns ``(rows, hidden)``: the candidates, and how many were left out
    because no invitation could ever reach them. The count is returned rather
    than the accounts because they are not work in progress - the admin user
    list is where those get dealt with - but a page that silently showed fewer
    accounts than exist would be lying.
    """
    linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))
    population = list(all_candidates().order_by("username"))
    flags = flag_users(population)

    unlinked = [
        row for row in rows(population, flags=flags) if row.user.pk not in linked
    ]
    candidates = [row for row in unlinked if row.enrollable]
    return candidates, len(unlinked) - len(candidates)


def publishers():
    """Who holds ``publish_qgisfeedentry``, in one query.

    ``user.has_perm`` cannot be prefetched, so asking it per row would be two
    queries per account. This asks the same question of everybody at once.
    Inactive accounts are excluded because ``ModelBackend`` grants them no
    permissions, and the answers have to agree.
    """
    return set(
        User.objects.filter(
            Q(
                user_permissions__codename=PUBLISH_PERMISSION,
                user_permissions__content_type__app_label=PUBLISH_APP,
            )
            | Q(
                groups__permissions__codename=PUBLISH_PERMISSION,
                groups__permissions__content_type__app_label=PUBLISH_APP,
            ),
            is_active=True,
        ).values_list("pk", flat=True)
    )


def all_candidates():
    """Every account the migration considers, with groups prefetched.

    ``proposed_roles`` and the duplicate checks both read groups, so fetching
    them here is what keeps the page to a fixed number of queries.
    """
    return User.objects.all().prefetch_related("groups")
