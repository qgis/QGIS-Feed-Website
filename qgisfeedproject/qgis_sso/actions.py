# coding=utf-8
"""Run an enrolment step over a selection of accounts.

The admin actions and the people pages take their guards, their order and
their messages from here, so the two never drift apart. Nothing here reads the
request.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .enrolment import publishers
from .keycloak import KeycloakError
from .migration import flag_users
from .models import KeycloakIdentity
from .provisioning import (
    ALREADY_DISABLED,
    DISABLED,
    NOT_YET_PROVEN,
    Provisioner,
    disable_local_password,
    setup_redirect_uri,
)

#: Each account costs several synchronous Keycloak calls, and there is no task queue.
DEFAULT_MAX_USERS_PER_ACTION = 25

#: Lookups in flight at once. Enough to speed a wave up, too few to flood the realm.
DEFAULT_LOOKUP_CONCURRENCY = 8

#: Outcome levels, named after what the reader should do about them.
OK = "ok"
HELD_BACK = "held-back"
FAILED = "failed"


def max_users_per_action():
    return getattr(settings, "SSO_ADMIN_ACTION_MAX_USERS", DEFAULT_MAX_USERS_PER_ACTION)


def lookup_concurrency():
    return getattr(settings, "SSO_LOOKUP_CONCURRENCY", DEFAULT_LOOKUP_CONCURRENCY)


def inspect_concurrently(decide, users):
    """Run ``decide`` over ``users`` in a thread pool, keeping their order.

    A single user runs on this thread, because a pool is not worth it.
    """
    if len(users) < 2:
        return [decide(user) for user in users]

    workers = min(lookup_concurrency(), len(users))
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="sso-inspect"
    ) as pool:
        # map re-raises a worker's exception, so the caller's handler still sees it.
        return list(pool.map(decide, users))


def is_sso_administrator(user):
    """Whether this user may write to the shared realm or retire a login method.

    Superusers only, because these actions can grant roles on this site.
    """
    return bool(user and user.is_active and user.is_superuser)


@dataclass
class Outcome:
    """What happened to one account, and what to tell the reader."""

    username: str
    level: str = OK
    message: str = ""
    #: Only ever set by :func:`issue_setup_links`, and never persisted.
    link: str = ""

    @property
    def ok(self):
        return self.level == OK

    @property
    def failed(self):
        return self.level == FAILED


@dataclass
class Run:
    """The result of one action over a selection."""

    outcomes: list = field(default_factory=list)

    @property
    def succeeded(self):
        return [outcome for outcome in self.outcomes if outcome.ok]

    @property
    def problems(self):
        return [outcome for outcome in self.outcomes if not outcome.ok]

    def add(self, username, level=OK, message="", link=""):
        self.outcomes.append(Outcome(username, level, message, link))
        return self.outcomes[-1]


def plan_provisioning(users, include_flagged=False):
    """Decide what would happen to each user, writing nothing.

    Returns ``(provisioner, decisions)``. Raises
    :class:`~qgis_sso.keycloak.KeycloakError` if the realm cannot be reached,
    because there is nothing useful to show without it.
    """
    # Re-fetch with groups prefetched, keeping the order people were picked in.
    users = list(users)
    prefetched = (
        User.objects.filter(pk__in=[user.pk for user in users])
        .prefetch_related("groups")
        .in_bulk()
    )
    users = [prefetched[user.pk] for user in users if user.pk in prefetched]

    session = Provisioner()
    flags = flag_users(users)

    # Nothing below may query the database. A query from a worker thread opens
    # its own connection.
    linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))
    linked_subs = set(KeycloakIdentity.objects.values_list("sub", flat=True))
    can_publish = publishers()

    # Fetch the token here, or every worker would ask for one at once.
    session.client.access_token()

    def decide(user):
        return session.inspect(
            user,
            flags=flags.get(user.pk, []),
            include_flagged=include_flagged,
            linked_ids=linked,
            linked_subs=linked_subs,
            can_publish=user.pk in can_publish,
        )

    return session, inspect_concurrently(decide, users)


def run_provisioning(session, decisions):
    """Create or link each account a decision allows. Sends no email.

    Each message says whether we created or linked, and names every account
    held back with its reason.
    """
    run = Run()
    for decision in decisions:
        if not decision.actionable:
            run.add(
                decision.user.username,
                FAILED if decision.is_error else HELD_BACK,
                decision.reason,
            )
            continue

        linking = decision.is_link
        try:
            if linking:
                session.link(decision)
            else:
                session.provision(decision)
        except KeycloakError as error:
            run.add(
                decision.username,
                FAILED,
                (
                    _("could not be linked: %s") % error
                    if linking
                    else _("could not be created: %s") % error
                ),
            )
            continue

        if linking:
            run.add(
                decision.username,
                OK,
                _("linked to the existing QGIS account %(username)s")
                % {"username": decision.realm_username},
            )
        else:
            run.add(decision.username, OK, _("account created"))
    return run


def send_setup_emails(session, identities, redirect_uri=None):
    """Email each identity its setup link. One failure does not stop the rest."""
    redirect_uri = redirect_uri or setup_redirect_uri()
    run = Run()
    for identity in identities:
        try:
            session.send_setup_link(identity, redirect_uri)
        except KeycloakError as error:
            run.add(identity.user.username, FAILED, str(error))
            continue
        run.add(identity.user.username, OK, _("setup email sent"))
    return run


def issue_setup_links(session, identities, redirect_uri=None):
    """Fetch each identity's setup link for an administrator to hand over.

    The link is a bearer credential, so it travels on the outcome and nowhere
    else. Once somebody has signed in, a link would sign the holder in as them
    with no passkey, so those accounts are held back.
    """
    redirect_uri = redirect_uri or setup_redirect_uri()
    run = Run()
    for identity in identities:
        if identity.has_logged_in_via_sso:
            run.add(
                identity.user.username,
                HELD_BACK,
                _(
                    "has already signed in and has a passkey. A link would sign "
                    "you in as them, on every QGIS site. Send the setup email "
                    "instead. That reaches them, not you."
                ),
            )
            continue
        try:
            link = session.issue_setup_link(identity, redirect_uri)
        except KeycloakError as error:
            run.add(identity.user.username, FAILED, str(error))
            continue
        run.add(identity.user.username, OK, _("link issued"), link=link)
    return run


def retire_passwords(identities):
    """Make the local password unusable where SSO is proven to work.

    Returns ``(run, counts)``: one line per account, and the totals.
    """
    run = Run()
    counts = {DISABLED: 0, ALREADY_DISABLED: 0, NOT_YET_PROVEN: 0}
    for identity in identities:
        result = disable_local_password(identity)
        counts[result] += 1
        if result == DISABLED:
            run.add(identity.user.username, OK, _("local password disabled"))
        elif result == ALREADY_DISABLED:
            run.add(identity.user.username, OK, _("already had no usable password"))
        elif result == NOT_YET_PROVEN:
            run.add(
                identity.user.username,
                HELD_BACK,
                _(
                    "has never signed in through SSO, so removing the password "
                    "would lock them out"
                ),
            )
    return run, counts
