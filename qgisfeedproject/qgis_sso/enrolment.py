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
from django.db.models import Case, CharField, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce, Concat, NullIf, Trim
from django.utils.translation import gettext_lazy as _

from . import tiers
from .migration import flag_users, proposed_roles, proposed_username
from .models import KeycloakIdentity, LinkMethod, TrustState

#: The permission that makes somebody a reviewer in Keycloak. Matched on the
#: app label too, so a same-named permission in another app cannot grant it.
PUBLISH_APP = "qgisfeed"
PUBLISH_PERMISSION = "publish_qgisfeedentry"

BLOCKED = "blocked"
NOT_PROVISIONED = "not-provisioned"
PROVISIONED = "provisioned"
LINK_SENT = "link-sent"
ACTIVE = "active"
SUSPENDED = "suspended"
REVOKED = "revoked"

#: Label and Bulma modifier per state. The label carries the meaning; the
#: colour only repeats it, so the table stays readable without it.
#:
#: These say where an account has got to, and nothing about how it arrived -
#: that is the sponsor column. There used to be a "Migrated" state, which
#: conflated the two: it meant "local password retired", which can only ever
#: happen to a grandfathered account and says nothing about progress. It is a
#: marker on the row now.
STATES = {
    BLOCKED: (_("Needs a decision"), "is-danger"),
    NOT_PROVISIONED: (_("No QGIS account"), "is-light"),
    PROVISIONED: (_("Created"), "is-info"),
    LINK_SENT: (_("Link sent"), "is-warning"),
    ACTIVE: (_("Active"), "is-success"),
    SUSPENDED: (_("Suspended"), "is-warning"),
    REVOKED: (_("Revoked"), "is-danger"),
}

#: The states an account with a realm identity can be in, in the order it
#: passes through them, which is also the order the filter offers. ``BLOCKED``
#: and ``NOT_PROVISIONED`` describe accounts that have no identity yet; those
#: live on the invite page, which does not filter by state.
LINKED_STATES = [PROVISIONED, LINK_SENT, ACTIVE, SUSPENDED, REVOKED]

#: Flags that put an account beyond enrolment altogether. Without an address
#: there is nowhere to send the invitation, and a deactivated account is one
#: somebody deliberately closed. Both belong in the admin user list, not on a
#: page about moving people forward.
#:
#: The other flags stay: a dormant account and one nobody has ever signed in to
#: can both be enrolled, and the flag is only there to say so.
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
    #: Set by the enrolment view, which knows who is looking. False here so a
    #: row built anywhere else never offers the action by accident.
    may_revoke: bool = False
    may_reparent: bool = False
    may_sponsor: bool = False
    #: True for a row the reader neither vouched for nor administers, which is
    #: only ever a suspended account they found by typing the name in full. They
    #: need the name to make the offer and nothing else, so nothing else shows.
    hide_contact: bool = False

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
    def sponsor(self):
        return getattr(self.identity, "sponsor", None)

    @property
    def password_retired(self):
        """Only ever true of a grandfathered account, and orthogonal to state."""
        return bool(getattr(self.identity, "local_password_disabled_at", None))

    @property
    def pending(self):
        """True for an account that exists in the realm and nobody has used.

        What the setup email and the enrolment link are for, and what holds a
        place against a sponsor's quota. Grandfathered accounts count: they have
        no sponsor, and the migration wave still has to reach them.

        Neither is offered once somebody has signed in. There is nothing left to
        set up, and a link handed to them then would authenticate with no
        passkey.
        """
        identity = self.identity
        if identity is None:
            return False
        return (
            identity.first_sso_login_at is None
            and identity.trust_state == TrustState.ACTIVE
        )

    @property
    def cancellable(self):
        """True for a pending invitation this site created in full.

        ``INVITATION`` is written in one place only, the invite form, which is
        also the only path that creates the Django user and the realm account
        together. Everything else - the migration wave, an invitation to
        somebody who already had an account here - keeps its own records, so
        cancelling must not reach them.
        """
        identity = self.identity
        return (
            self.pending
            and identity.sponsor_id is not None
            and identity.link_method == LinkMethod.INVITATION
        )

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
    # Trust overrides progress: somebody revoked has not got anywhere, whatever
    # they had reached before.
    if identity.trust_state == TrustState.REVOKED:
        return REVOKED
    if identity.trust_state == TrustState.SUSPENDED:
        return SUSPENDED
    if identity.first_sso_login_at is not None:
        return ACTIVE
    if identity.setup_email_sent_at or identity.setup_link_issued_at:
        return LINK_SENT
    return PROVISIONED


#: The same cascade as :func:`state_of`, written so the database can answer it.
#: The list page filters and sorts on this, so a test asserts the two agree
#: state for state: a filter that disagrees with the tag beside it would be
#: worse than no filter at all.
#:
#: ``BLOCKED`` and ``NOT_PROVISIONED`` are missing on purpose. Both describe an
#: account with no identity, and this expression only ever runs on identities.
STATE_EXPRESSION = Case(
    When(trust_state=TrustState.REVOKED, then=Value(REVOKED)),
    When(trust_state=TrustState.SUSPENDED, then=Value(SUSPENDED)),
    When(first_sso_login_at__isnull=False, then=Value(ACTIVE)),
    When(
        Q(setup_email_sent_at__isnull=False) | Q(setup_link_issued_at__isnull=False),
        then=Value(LINK_SENT),
    ),
    default=Value(PROVISIONED),
    output_field=CharField(),
)

#: The states as a number, so the column sorts by how far somebody has got
#: rather than by the alphabet. Built from ``LINKED_STATES`` so the order on
#: screen and the order in the filter cannot drift apart.
STATE_ORDER = Case(
    *[When(state=name, then=Value(rank)) for rank, name in enumerate(LINKED_STATES)],
    default=Value(len(LINKED_STATES)),
    output_field=IntegerField(),
)

#: The sponsor as the column shows them, which is also what it sorts on:
#: their full name, falling back to their username. Null exactly when there is
#: no sponsor, which is what keeps those rows together at one end.
SPONSOR_NAME = Coalesce(
    NullIf(
        Trim(Concat("sponsor__first_name", Value(" "), "sponsor__last_name")),
        Value(""),
    ),
    "sponsor__username",
)


def linked_identities(sponsored_by=None, suspended_named=""):
    """Accounts that exist in the realm, as a queryset the database can page.

    Everything the list filters or sorts on is resolved here, so answering a
    question about ten rows never means reading the user table. The rows for
    one page are built by :func:`rows_for` once the database has chosen them.

    ``sponsored_by`` narrows the list to the accounts one person vouched for,
    which is what everybody who is not a superuser sees.

    ``suspended_named`` adds back one suspended account, by its full username.
    A colleague may offer to sponsor somebody a cascade suspended, and they
    cannot do that without finding them, but a contributor has no business
    browsing accounts they had nothing to do with. So the name has to be typed
    in full: a partial match returns nothing, and the row hides the address.
    """
    identities = KeycloakIdentity.objects.annotate(
        state=STATE_EXPRESSION, sponsor_name=SPONSOR_NAME
    )
    if sponsored_by is not None:
        mine = Q(sponsor=sponsored_by)
        if suspended_named:
            mine |= Q(
                trust_state=TrustState.SUSPENDED,
                user__username__iexact=suspended_named,
            )
        identities = identities.filter(mine)
    return identities


def rows_for(identities):
    """One :class:`Row` per identity, for the rows on one page.

    The state comes from the annotation the queryset carried rather than being
    worked out again here, so the tag on a row and the filter that selected it
    cannot disagree.

    These accounts already exist in the realm, so they carry no flags. Flags
    answer "should we create this account", which is the invite page's
    question, and :func:`candidate_rows` is where it gets asked.
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
    """Build one :class:`Row` per user, without an N+1.

    Everything it needs is asked of the database once for the whole batch, and
    nothing it asks reaches past the accounts it was given.
    """
    users = list(users)
    if flags is None:
        flags = flag_users(users)

    # Both of these are one query for the whole batch and neither reaches past
    # it. Asking per row would be an N+1; asking for every account in the site
    # would make a page of ten cost the whole table.
    identities = {
        identity.user_id: identity
        for identity in KeycloakIdentity.objects.select_related("sponsor").filter(
            user__in=users
        )
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
    because no invitation could ever reach them. The count is returned rather
    than the accounts because they are not work in progress - the admin user
    list is where those get dealt with - but a page that silently showed fewer
    accounts than exist would be lying.
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

    ``user.has_perm`` cannot be prefetched, so asking it per row would be two
    queries per account. This asks the same question of a whole batch at once.
    Inactive accounts are excluded because ``ModelBackend`` grants them no
    permissions, and the answers have to agree.

    ``among`` narrows the question to the accounts a caller is showing, so a
    page of ten does not have to ask it of everybody.
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
