# coding=utf-8
"""Running an enrolment step over a selection of accounts.

Both callers - the Django admin actions and the enrolment page at
``/manage/sso/`` - need the same guards, the same order of operations and the
same words for what happened. Keeping that here means there is one set of rules
rather than two that drift, and it stays testable without a browser.

Nothing here talks to the request: functions take a selection and return per
account outcomes. Turning an outcome into an admin message or a table row is
the caller's business.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

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

#: Each account costs several synchronous round trips to Keycloak, inside one
#: request. Work in waves of this size; there is no task queue here.
DEFAULT_MAX_USERS_PER_ACTION = 25

#: Outcome levels, named after what the reader should do about them.
OK = "ok"
HELD_BACK = "held-back"
FAILED = "failed"


def max_users_per_action():
    return getattr(settings, "SSO_ADMIN_ACTION_MAX_USERS", DEFAULT_MAX_USERS_PER_ACTION)


def is_sso_administrator(user):
    """Who may write to the shared realm or retire a login method.

    Superusers only. The service account these actions use can create realm
    users and assign client roles, so anyone able to trigger them can grant
    privileges on this site - and the realm is shared with hub and plugins.
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

    Flags are computed against the whole population rather than the selection:
    a username case collision is only visible when the other account is in view
    too.

    Returns ``(provisioner, decisions)``. Raises
    :class:`~qgis_sso.keycloak.KeycloakError` if the realm cannot be reached,
    because there is nothing useful to show without it.
    """
    users = list(users)
    session = Provisioner()
    flags = flag_users(User.objects.all().prefetch_related("groups"))
    linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))

    decisions = [
        session.inspect(
            user,
            flags=flags.get(user.pk, []),
            include_flagged=include_flagged,
            linked_ids=linked,
        )
        for user in users
    ]
    return session, decisions


def run_provisioning(session, decisions):
    """Create the realm accounts the decisions call for. Sends no email.

    A held-back account is named along with its reason. Being told only that
    there was nothing to do leaves no way to see which account was skipped, or
    for what, without previewing again.
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
        try:
            session.provision(decision)
        except KeycloakError as error:
            run.add(
                decision.username,
                FAILED,
                _("could not be created: %s") % error,
            )
            continue
        run.add(decision.username, OK, _("account created"))
    return run


def send_setup_emails(session, identities, redirect_uri=None):
    """Email each identity its setup link and record the send.

    One failure does not abandon the rest: a wave that stops half way through
    is worse than one that reports what it could not do.
    """
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
    """Fetch each identity's sign-in link for an administrator to hand over.

    The link is carried on the outcome and nowhere else. It is a bearer
    credential: whoever holds it signs in as that person, so it is never
    written to the database, never logged and never put through the messages
    framework, whose fallback storage is a cookie.

    Only for accounts that have not signed in yet. For one that has, the
    required actions are already spent, so the link authenticates straight
    through with no passkey and opens a session across the whole realm - hub
    and plugins included. That is not an invitation, it is a way to become
    somebody. Their recovery is the setup email, which reaches them rather than
    the administrator asking for it.
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
                    "you in as them, across the whole realm. Send the setup "
                    "email instead: it reaches them, not you."
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
    """Make the local Django password unusable where SSO is proven to work.

    Only accounts with a recorded successful SSO login are touched. Taking the
    password from somebody who has not yet signed in through Keycloak locks
    them out of an account they cannot recover on their own, and a passkey-only
    account has no password reset to fall back on.

    Returns ``(run, counts)``: one line per account for a page that lists them,
    and the totals for a caller that reports a summary.
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
                    "never signed in through SSO; removing the password would "
                    "lock them out"
                ),
            )
    return run, counts
