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
from .tiers import effective_tier

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
    should be impossible - a sponsor is set once, at creation - but finding out
    otherwise at recursion depth is not the way to learn it.
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


def may_revoke(actor, identity):
    """Whether ``actor`` may withdraw trust from ``identity``.

    An inviter may act anywhere in their own subtree and nowhere else - never
    on an ancestor, never across branches. A root may act anywhere in the
    forest except on another root, because roots are peers and removing one
    needs a second root to agree (US-5.5, not built).
    """
    if identity.user_id == actor.pk:
        # Standing down is US-5.3 and behaves differently: descendants are
        # re-parented rather than suspended.
        return False
    if identity.is_root:
        return False

    actor_identity = getattr(actor, "keycloak_identity", None)
    if actor_identity is None:
        return False
    if actor_identity.trust_state != TrustState.ACTIVE:
        return False

    if actor.is_superuser and effective_tier(actor) == 0:
        return True

    return any(node.user_id == actor.pk for node in ancestors(identity))


def preview(actor, identity):
    """Who this would affect, by name, before anything happens.

    US-5.2 asks for no surprise blast radius, and a cascade that turns out
    larger than expected is the one mistake here that cannot be walked back
    casually.
    """
    if not may_revoke(actor, identity):
        raise RevocationError(_("You cannot withdraw trust from that account."))
    return Blast(
        target=identity,
        descendants=[
            node for node in subtree(identity) if node.trust_state == TrustState.ACTIVE
        ],
    )


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


def restore(actor, identity, client=None):
    """Give back what a revocation took, subtree included.

    Only inside the grace window. US-5.2 asks for one action, so the subtree
    comes back with the account that took it down - but only the accounts this
    revocation suspended, not any that were already suspended for their own
    reasons.
    """
    if identity.trust_state != TrustState.REVOKED:
        raise RevocationError(_("That account has not been revoked."))
    if not may_revoke(actor, identity):
        raise RevocationError(_("You cannot restore that account."))
    if identity.revoked_at and timezone.now() - identity.revoked_at > grace():
        raise RevocationError(
            _(
                "This revocation is too old to undo in one step. Invite the "
                "person again instead."
            )
        )

    roles = list(identity.roles_at_revocation or [])
    with transaction.atomic():
        restore_in_realm(identity, roles, client=client)

        revoked_at = identity.revoked_at
        for node in [identity] + subtree(identity):
            if node.pk != identity.pk:
                # Leave alone anything suspended by a different revocation, or
                # revoked in its own right.
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
            _("auth.qgis.org could not be reached, so nothing was changed.")
        )
    realm, uuid = session
    try:
        if roles:
            realm.remove_client_roles(identity.sub, uuid, _known(realm, uuid, roles))
        realm.end_sessions(identity.sub)
        realm.set_user_enabled(identity.sub, False)
    except KeycloakError as error:
        raise RevocationError(
            _("auth.qgis.org refused the change, so nothing was done: %s") % error
        )


def restore_in_realm(identity, roles, client=None):
    session = _session(client)
    if session is None:
        raise RevocationError(
            _("auth.qgis.org could not be reached, so nothing was changed.")
        )
    realm, uuid = session
    try:
        realm.set_user_enabled(identity.sub, True)
        if roles:
            realm.assign_client_roles(identity.sub, uuid, _known(realm, uuid, roles))
    except KeycloakError as error:
        raise RevocationError(
            _("auth.qgis.org refused the change, so nothing was done: %s") % error
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
