# coding=utf-8
"""Where each account has got to in the migration to single sign-on.

Everything here reads the local database, so the people pages never wait on
Keycloak to render. An account moves through the states in order.
"""

from dataclasses import dataclass, field

from django.contrib.auth.models import User
from django.db.models import Case, CharField, IntegerField, Q, Value, When
from django.utils.translation import gettext_lazy as _

from . import tiers
from .migration import flag_users, proposed_roles, proposed_username
from .models import KeycloakIdentity

#: The permission that makes somebody a reviewer in Keycloak. Matched on the
#: app label too, so a same-named permission in another app cannot grant it.
PUBLISH_APP = "qgisfeed"
PUBLISH_PERMISSION = "publish_qgisfeedentry"

BLOCKED = "blocked"
NOT_PROVISIONED = "not-provisioned"
PROVISIONED = "provisioned"
LINK_SENT = "link-sent"
ACTIVE = "active"

#: Label and Bulma modifier per state. The colour only repeats the label.
STATES = {
    BLOCKED: (_("Needs a decision"), "is-danger"),
    NOT_PROVISIONED: (_("No QGIS account"), "is-light"),
    PROVISIONED: (_("Created"), "is-info"),
    LINK_SENT: (_("Link sent"), "is-warning"),
    ACTIVE: (_("Active"), "is-success"),
}

#: The states of an account with a realm identity, in the order it passes them.
LINKED_STATES = [PROVISIONED, LINK_SENT, ACTIVE]

#: Flags no invitation can get past. These accounts belong in the admin user list.
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
    def role_labels(self):
        """The roles as a reader sees them, not as Keycloak names them."""
        return tiers.labels(self.proposed_roles)

    @property
    def css_class(self):
        return STATES[self.state][1]

    @property
    def password_retired(self):
        """Whether the local password is retired. It says nothing about state."""
        return bool(getattr(self.identity, "local_password_disabled_at", None))

    @property
    def pending(self):
        """True for a realm account nobody has signed in to yet.

        Only these are offered the setup email and the setup link.
        """
        identity = self.identity
        if identity is None:
            return False
        return identity.first_sso_login_at is None

    @property
    def enrollable(self):
        """False for an account no invitation could ever reach."""
        return not HIDDEN_FLAGS.intersection(self.flags)


def state_of(identity, flags):
    """Which state one account is in. A flag only blocks an unprovisioned one."""
    if identity is None:
        return BLOCKED if flags else NOT_PROVISIONED
    if identity.first_sso_login_at is not None:
        return ACTIVE
    if identity.setup_email_sent_at or identity.setup_link_issued_at:
        return LINK_SENT
    return PROVISIONED


#: :func:`state_of` for the database, on identities only. A test keeps the two equal.
STATE_EXPRESSION = Case(
    When(first_sso_login_at__isnull=False, then=Value(ACTIVE)),
    When(
        Q(setup_email_sent_at__isnull=False) | Q(setup_link_issued_at__isnull=False),
        then=Value(LINK_SENT),
    ),
    default=Value(PROVISIONED),
    output_field=CharField(),
)

#: The states as a number, so the column sorts by progress and not by the alphabet.
STATE_ORDER = Case(
    *[When(state=name, then=Value(rank)) for rank, name in enumerate(LINKED_STATES)],
    default=Value(len(LINKED_STATES)),
    output_field=IntegerField(),
)


def linked_identities():
    """Accounts that exist in the realm, as a queryset the database can page."""
    return KeycloakIdentity.objects.annotate(state=STATE_EXPRESSION)


def rows_for(identities):
    """One :class:`Row` per identity on one page.

    The state comes from the queryset's annotation, so a row's tag always
    matches the filter. These accounts carry no flags, because flags only
    matter before an account is created.
    """
    identities = list(identities)
    users = [identity.user for identity in identities]
    can_publish = publishers(among=[user.pk for user in users])

    return [
        Row(
            user=identity.user,
            identity=identity,
            state=identity.state,
            proposed_username=proposed_username(identity.user),
            proposed_roles=proposed_roles(
                identity.user, identity.user.pk in can_publish
            ),
        )
        for identity in identities
    ]


def rows(users, flags=None):
    """Build one :class:`Row` per user, with one query per batch and not per row."""
    users = list(users)
    if flags is None:
        flags = flag_users(users)

    identities = {
        identity.user_id: identity
        for identity in KeycloakIdentity.objects.filter(user__in=users)
    }
    can_publish = publishers(among=[user.pk for user in users])

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


def candidate_rows():
    """Accounts with no realm identity, split into the ones worth offering.

    Returns ``(rows, hidden)``: the candidates, and how many were left out
    because no invitation could reach them. The page says how many it hides.
    """
    unlinked = list(
        User.objects.filter(keycloak_identity__isnull=True)
        .prefetch_related("groups")
        .order_by("username")
    )
    built = rows(unlinked, flags=flag_users(unlinked))
    candidates = [row for row in built if row.enrollable]
    return candidates, len(unlinked) - len(candidates)


def publishers(among=None):
    """Who holds ``publish_qgisfeedentry``, in one query.

    ``user.has_perm`` costs two queries per account. Inactive accounts are left
    out, because ``ModelBackend`` grants them nothing. ``among`` narrows the
    question to the accounts on screen.
    """
    holders = User.objects.filter(
        Q(
            user_permissions__codename=PUBLISH_PERMISSION,
            user_permissions__content_type__app_label=PUBLISH_APP,
        )
        | Q(
            groups__permissions__codename=PUBLISH_PERMISSION,
            groups__permissions__content_type__app_label=PUBLISH_APP,
        ),
        is_active=True,
    )
    if among is not None:
        holders = holders.filter(pk__in=among)
    return set(holders.values_list("pk", flat=True))
