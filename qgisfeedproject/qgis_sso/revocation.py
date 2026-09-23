# coding=utf-8
"""Withdrawing trust, and giving it back.

Two outcomes, kept apart because they mean different things. The account
somebody acted on is **revoked**: its realm roles are removed, its sessions
ended and its realm account disabled, so the block does not depend on this site
being consulted. Everybody that account vouched for is **suspended**: they can
still sign in and see why, but hold no permissions, because they have done
nothing wrong.

Suspension is enforced locally, by :func:`qgis_sso.roles.mirror_roles` granting
nothing while it lasts. That is not a shortcut - it is the only thing that
works. Removing somebody's Django groups achieves nothing on its own, because
mirroring reconciles them from the token at the next sign-in and hands
everything straight back.

No view code here, so the rules are testable without a browser.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from .keycloak import KeycloakError
from .migration import feed_client_id
from .models import KeycloakIdentity, SsoAuditEvent, TrustState
from .roles import mirror_roles
from .tiers import effective_tier, may_invite, remaining, tier_of

logger = logging.getLogger(__name__)

#: How long a revocation can be undone in one action. After it, the record
#: stays but the button goes: reversing months later is a re-invitation, not
#: an undo.
DEFAULT_GRACE_DAYS = 7

#: A tree this deep is a bug, not a community. The walk stops rather than
#: recursing until Python does.
MAX_DEPTH = 32


class RevocationError(Exception):
    """A revocation was refused, and why, in words for the reader."""


def grace():
    return timedelta(
        days=getattr(settings, "SSO_REVOCATION_GRACE_DAYS", DEFAULT_GRACE_DAYS)
    )


@dataclass
class Blast:
    """What a revocation would do, before it does any of it."""

    target: object
    descendants: list = field(default_factory=list)

    @property
    def total(self):
        return 1 + len(self.descendants)


def subtree(identity):
    """Every identity below this one, breadth first.

    Guarded twice: a depth cap, and a set of everything already seen. A cycle
    is meant to be impossible - :func:`reparent` is the only thing that moves a
    sponsor after creation, and it refuses any move into the subtree - but
    finding out otherwise at recursion depth is not the way to learn it.
    """
    found = []
    seen = {identity.pk}
    frontier = [identity.user_id]

    for _depth in range(MAX_DEPTH):
        if not frontier:
            break
        children = list(
            KeycloakIdentity.objects.filter(sponsor_id__in=frontier)
            .exclude(pk__in=seen)
            .select_related("user", "sponsor")
        )
        if not children:
            break
        found.extend(children)
        seen.update(child.pk for child in children)
        frontier = [child.user_id for child in children]
    else:
        logger.error(
            "Trust graph deeper than %d below %s; stopped walking",
            MAX_DEPTH,
            identity.sub,
        )

    return found


def ancestors(identity):
    """Every identity above this one, nearest first."""
    chain = []
    seen = {identity.pk}
    current = identity

    for _depth in range(MAX_DEPTH):
        if current.sponsor_id is None:
            break
        parent = (
            KeycloakIdentity.objects.filter(user_id=current.sponsor_id)
            .exclude(pk__in=seen)
            .select_related("user")
            .first()
        )
        if parent is None:
            break
        chain.append(parent)
        seen.add(parent.pk)
        current = parent

    return chain


def refusal(actor, identity):
    """Why ``actor`` may not withdraw trust from ``identity``, or None.

    The reason is the useful part, so it is what this returns and
    :func:`may_revoke` is defined in terms of it. One set of rules, one place
    to change them, and the page can say which one stopped you.

    An inviter reaches their own subtree and nowhere else. An administrator
    reaches the whole forest. Neither reaches upwards, and neither reaches
    another administrator.
    """
    if identity.user_id == actor.pk:
        # Standing down is US-5.3 and behaves differently: descendants are
        # re-parented rather than suspended.
        return _("You cannot withdraw trust from your own account.")

    actor_identity = getattr(actor, "keycloak_identity", None)
    if actor_identity is None or actor_identity.trust_state != TrustState.ACTIVE:
        return _("Your account cannot withdraw trust.")

    # Never upwards. This used to sit after the administrator branch below,
    # which returns early, so it never ran for an administrator: they could
    # revoke whoever invited them and be suspended by their own cascade.
    if any(node.user_id == identity.user_id for node in ancestors(actor_identity)):
        return _(
            "You cannot withdraw trust from the person who invited you. "
            "Ask another administrator."
        )

    # Never sideways either. Removing an administrator is meant to need a
    # second administrator to agree (US-5.5), which is not built, so the case
    # is refused rather than half allowed. `is_root` was doing this job and
    # could not: nothing outside the tests ever sets it.
    if identity.is_root or tier_of(identity) == 0:
        return _(
            "You cannot withdraw trust from an administrator. Remove their "
            "administrator role first, or ask another administrator."
        )

    if out_of_reach(actor, identity):
        return _("You can only withdraw trust from people you invited.")
    return None


def out_of_reach(actor, identity):
    """Whether ``identity`` is outside what ``actor`` may act on at all.

    Reach only, shared by withdrawing and restoring. An administrator reaches
    the whole forest, everybody else reaches their own subtree. The guards that
    apply to withdrawing but not to restoring live in :func:`refusal`, because
    giving trust back is not the dangerous direction: an administrator who was
    wrongly revoked has to stay restorable.
    """
    actor_identity = getattr(actor, "keycloak_identity", None)
    if actor_identity is None or actor_identity.trust_state != TrustState.ACTIVE:
        return True
    if actor.is_superuser and effective_tier(actor) == 0:
        return False
    return not any(node.user_id == actor.pk for node in ancestors(identity))


def may_revoke(actor, identity):
    """Whether ``actor`` may withdraw trust from ``identity``."""
    return refusal(actor, identity) is None


def preview(actor, identity):
    """Who this would affect, by name, before anything happens.

    US-5.2 asks for no surprise blast radius, and a cascade that turns out
    larger than expected is the one mistake here that cannot be walked back
    casually.
    """
    refused = refusal(actor, identity)
    if refused:
        raise RevocationError(refused)

    blast = Blast(
        target=identity,
        descendants=[
            node for node in subtree(identity) if node.trust_state == TrustState.ACTIVE
        ],
    )
    # Belt and braces. The ancestor rule already makes this impossible, but the
    # action is irreversible and suspending yourself is the one outcome nobody
    # can undo, so it is checked rather than reasoned about.
    if any(node.user_id == actor.pk for node in blast.descendants):
        raise RevocationError(
            _("This would suspend your own account, so it was not done.")
        )
    return blast


@transaction.atomic
def revoke(actor, identity, reason, client=None):
    """Withdraw trust, and suspend everyone below.

    The realm calls happen first: if they fail there is nothing to undo locally,
    whereas a local record of a revocation that never reached Keycloak is a lie
    that leaves somebody signed in.
    """
    blast = preview(actor, identity)
    if not (reason or "").strip():
        raise RevocationError(_("Give a reason. It goes in the audit trail."))

    roles = list(identity.last_seen_roles or [])
    withdraw_in_realm(identity, roles, client=client)

    now = timezone.now()
    identity.trust_state = TrustState.REVOKED
    identity.revoked_at = now
    identity.revoked_by = actor
    identity.revocation_reason = reason.strip()
    identity.roles_at_revocation = roles
    identity.revoked_directly = True
    identity.save(
        update_fields=[
            "trust_state",
            "revoked_at",
            "revoked_by",
            "revocation_reason",
            "roles_at_revocation",
            "revoked_directly",
        ]
    )
    # The roles Keycloak knew about are gone, but this field is what the tier
    # is read from, and a revoked account never signs in again to refresh it.
    # Left in place, they keep their quota and go on inviting people.
    identity.last_seen_roles = []
    identity.save(update_fields=["last_seen_roles"])

    # Strip the local permissions now rather than at the next sign-in, because
    # there will not be one. mirror_roles is the same code that maintains them.
    mirror_roles(identity.user, [])

    identity.user.is_active = False
    # Rotating the password changes get_session_auth_hash, which Django checks
    # on every request, so every session this person has open dies at their
    # next click. These accounts already have unusable passwords; calling this
    # again picks a fresh random one, which is the point.
    identity.user.set_unusable_password()
    identity.user.save(update_fields=["is_active", "password"])

    for node in blast.descendants:
        node.trust_state = TrustState.SUSPENDED
        node.revoked_at = now
        node.revoked_by = actor
        node.revocation_reason = reason.strip()
        node.revoked_directly = False
        node.save(
            update_fields=[
                "trust_state",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "revoked_directly",
            ]
        )
        mirror_roles(node.user, [])
        SsoAuditEvent.record(
            SsoAuditEvent.Action.SUSPENDED,
            user=node.user,
            sub=node.sub,
            because_of=identity.user.username,
            by=actor.username,
        )

    SsoAuditEvent.record(
        SsoAuditEvent.Action.REVOKED_TRUST,
        user=identity.user,
        sub=identity.sub,
        by=actor.username,
        reason=reason.strip(),
        roles=roles,
        suspended=[node.user.username for node in blast.descendants],
    )

    # After the local writes, so a hook that fails cannot leave a half-done
    # revocation - the transaction takes the whole thing back with it.
    run_hook(identity.user)
    return blast


def reactivate(identity, revoked_at):
    """Bring a branch back from suspension, and say who came with it.

    Shared by :func:`restore` and :func:`reparent`, which undo the same
    cascade from opposite ends. ``revoked_at`` is the stamp every account the
    same revocation touched carries, so it is what tells them apart from an
    account suspended by some other revocation, or revoked in its own right.

    ``identity`` itself is always brought back and never filtered: the caller
    has already decided it should be, and its own state is the thing being
    undone rather than something to test.

    Purely local. ``revoke`` disables the realm account only for the account it
    acted on directly; the cascade below it never left this database, so
    nothing here needs Keycloak and nothing here can half fail against it.
    """
    brought_back = []
    for node in [identity] + subtree(identity):
        if node.pk != identity.pk:
            if node.trust_state != TrustState.SUSPENDED:
                continue
            if node.revoked_at != revoked_at:
                continue
        node.trust_state = TrustState.ACTIVE
        node.revoked_directly = False
        node.save(update_fields=["trust_state", "revoked_directly"])
        # Put the permissions back now. Mirroring would do it at their next
        # sign-in, but until then a restored account can see nothing, which
        # reads as the restore not having worked.
        mirror_roles(node.user, node.last_seen_roles)
        brought_back.append(node)
    return brought_back


def restore(actor, identity, client=None):
    """Give back what a revocation took, subtree included.

    Only inside the grace window. US-5.2 asks for one action, so the subtree
    comes back with the account that took it down - but only the accounts this
    revocation suspended, not any that were already suspended for their own
    reasons.
    """
    if identity.trust_state != TrustState.REVOKED:
        raise RevocationError(_("That account has not been revoked."))
    if identity.user_id == actor.pk or out_of_reach(actor, identity):
        raise RevocationError(_("You cannot restore that account."))
    if identity.revoked_at and timezone.now() - identity.revoked_at > grace():
        raise RevocationError(
            _(
                "This revocation is too old to undo in one step. To bring back "
                "somebody it suspended, move them to a new sponsor instead."
            )
        )

    roles = list(identity.roles_at_revocation or [])
    with transaction.atomic():
        restore_in_realm(identity, roles, client=client)

        reactivate(identity, identity.revoked_at)

        # The revoked account's roles were cleared so it could not go on
        # inviting; the record kept for exactly this moment puts them back.
        identity.last_seen_roles = roles
        identity.save(update_fields=["last_seen_roles"])
        mirror_roles(identity.user, roles)

        identity.user.is_active = True
        identity.user.save(update_fields=["is_active"])

        SsoAuditEvent.record(
            SsoAuditEvent.Action.RESTORED,
            user=identity.user,
            sub=identity.sub,
            by=actor.username,
            roles=roles,
        )


def may_reparent(actor):
    """Whether ``actor`` may move accounts between sponsors at all.

    US-5.4 asks for a web maintainer, so tier 1 and above. This is the rescue
    hatch for a cascade, and the people who can cause one should be able to
    undo it.
    """
    actor_identity = getattr(actor, "keycloak_identity", None)
    if actor_identity is None or actor_identity.trust_state != TrustState.ACTIVE:
        return False
    return bool(actor.is_superuser) and effective_tier(actor) <= 1


def eligible_sponsors(identity):
    """Accounts that could take this one on, for the picker.

    Excludes the account itself and everything below it, because a sponsor
    inside the subtree would close the graph into a loop. Also excludes anybody
    whose own tier is too low to hold the roles being moved.
    """
    roles = list(identity.last_seen_roles or [])
    barred = {identity.user_id} | {node.user_id for node in subtree(identity)}
    candidates = (
        KeycloakIdentity.objects.filter(trust_state=TrustState.ACTIVE)
        .exclude(user_id__in=barred)
        .select_related("user")
        .order_by("user__username")
    )
    offered = []
    for candidate in candidates:
        # may_invite reads the tier off the user's identity, which is the row
        # already in hand. Priming the cache keeps this one query rather than
        # one per candidate.
        candidate.user.keycloak_identity = candidate
        if all(may_invite(candidate.user, role) for role in roles):
            offered.append(candidate)
    return offered


def reparent(actor, identity, new_sponsor, reason):
    """Move an account to a different sponsor, rescuing it if it was suspended.

    US-5.4, and the way out of a cascade once the grace window has closed. The
    window deliberately does not apply here: after it, restoring is refused and
    this is the only path left, so gating it the same way would leave suspended
    people with nothing at all.

    Rescuing is the point, so a suspended account comes back together with
    everybody the same revocation suspended below it.
    """
    if not may_reparent(actor):
        raise RevocationError(_("Your account cannot move people to a new sponsor."))
    if not (reason or "").strip():
        raise RevocationError(_("Give a reason. It goes in the audit trail."))

    # Read the sponsor's standing from the database rather than from whatever
    # is cached on the user handed in. Creating an identity caches it on the
    # user, and a caller holding that instance from before a revocation would
    # otherwise see a revoked account as active and let it take somebody on.
    sponsor_identity = KeycloakIdentity.objects.filter(user=new_sponsor).first()
    if sponsor_identity is None or sponsor_identity.trust_state != TrustState.ACTIVE:
        raise RevocationError(_("That account cannot sponsor anybody right now."))
    # Everything below reads the tier and the quota through the user, which
    # goes back to the same relation. Point it at the row just fetched.
    new_sponsor.keycloak_identity = sponsor_identity

    if new_sponsor.pk == identity.user_id:
        raise RevocationError(_("An account cannot sponsor itself."))
    if any(node.user_id == new_sponsor.pk for node in subtree(identity)):
        # A sponsor from inside the subtree would make the chain a loop, and
        # every walk in this module would then depend on its depth cap to stop.
        raise RevocationError(
            _("That person was invited by this account, so they cannot sponsor it.")
        )

    roles = list(identity.last_seen_roles or [])
    refused = [role for role in roles if not may_invite(new_sponsor, role)]
    if refused:
        raise RevocationError(
            _("%(username)s cannot sponsor somebody with the role %(roles)s.")
            % {"username": new_sponsor.username, "roles": ", ".join(refused)}
        )

    # Quota counts invitations still open, which means accounts that have
    # never signed in and whose place has not been given back. Moving somebody
    # who has already arrived, or whose place is already free, costs their new
    # sponsor nothing, so it is only checked for the rest.
    if identity.first_sso_login_at is None and identity.invitation_released_at is None:
        left = remaining(new_sponsor)
        if left is not None and left < 1:
            raise RevocationError(
                _(
                    "%(username)s has no free place for this account. They can "
                    "free one on the people page."
                )
                % {"username": new_sponsor.username}
            )

    was = identity.sponsor
    rescued = []
    with transaction.atomic():
        identity.sponsor = new_sponsor
        identity.save(update_fields=["sponsor"])

        if identity.trust_state == TrustState.SUSPENDED:
            rescued = reactivate(identity, identity.revoked_at)

        SsoAuditEvent.record(
            SsoAuditEvent.Action.REPARENTED,
            user=identity.user,
            sub=identity.sub,
            by=actor.username,
            reason=reason.strip(),
            was=was.username if was else "",
            now=new_sponsor.username,
            rescued=[node.user.username for node in rescued],
        )
    return rescued


# -- the realm side --------------------------------------------------------


def withdraw_in_realm(identity, roles, client=None):
    """Remove the roles, end the sessions, disable the account.

    All three, and in that order. Removing roles alone leaves an existing
    session working until its token expires; disabling alone leaves the roles
    to come back if the account is ever re-enabled.
    """
    session = _session(client)
    if session is None:
        raise RevocationError(
            _("We could not reach the QGIS account service, so nothing was changed.")
        )
    realm, uuid = session
    try:
        if roles:
            realm.remove_client_roles(identity.sub, uuid, _known(realm, uuid, roles))
        realm.end_sessions(identity.sub)
        realm.set_user_enabled(identity.sub, False)
    except KeycloakError as error:
        raise RevocationError(
            _("The QGIS account service refused the change, so nothing was done.")
            + f" ({error})"
        )


def restore_in_realm(identity, roles, client=None):
    session = _session(client)
    if session is None:
        raise RevocationError(
            _("We could not reach the QGIS account service, so nothing was changed.")
        )
    realm, uuid = session
    try:
        realm.set_user_enabled(identity.sub, True)
        if roles:
            realm.assign_client_roles(identity.sub, uuid, _known(realm, uuid, roles))
    except KeycloakError as error:
        raise RevocationError(
            _("The QGIS account service refused the change, so nothing was done.")
            + f" ({error})"
        )


def _session(client):
    """The realm client and this site's client UUID, or None."""
    from .keycloak import KeycloakAdminClient

    try:
        session = client or KeycloakAdminClient()
        uuid = session.client_uuid(feed_client_id())
    except KeycloakError:
        logger.exception("Could not reach the realm to change trust")
        return None
    return session, uuid


def _known(session, uuid, names):
    """Role representations for the names we hold, skipping any that vanished."""
    catalogue = session.client_roles(uuid)
    return [catalogue[name] for name in names if name in catalogue]


# -- what the site does about it -------------------------------------------


def run_hook(user):
    """Let the site decide what happens to the person's unpublished work.

    ``qgis_sso`` deliberately knows nothing about entries, statuses or reviews.
    ``SSO_ON_REVOKE`` names a callable that does; the feed's returns pending and
    approved entries to draft so revoked work cannot auto-publish.
    """
    path = getattr(settings, "SSO_ON_REVOKE", "")
    if not path:
        return
    import_string(path)(user)
