# coding=utf-8
"""Withdrawing trust, and giving it back.

The account somebody acts on is **revoked**: its realm roles are removed, its
sessions ended and its realm account disabled, so the block holds without this
site being asked. Everybody that account vouched for is **suspended**: they can
still sign in and see why, but hold no permissions, because they have done
nothing wrong.

Suspension is enforced locally, by :func:`qgis_sso.roles.mirror_roles` granting
nothing while it lasts. Removing somebody's Django groups would not last,
because mirroring rebuilds them from the token at the next sign-in.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from .keycloak import KeycloakAdminClient, KeycloakError
from .migration import feed_client_id
from .models import KeycloakIdentity, SsoAuditEvent, TrustState
from .roles import mirror_roles
from .tiers import effective_tier, tier_of

logger = logging.getLogger(__name__)

#: After this, the record stays, but reversing it takes a new invitation.
DEFAULT_GRACE_DAYS = 7

#: A tree this deep is a bug. The walk stops rather than recursing for ever.
MAX_DEPTH = 32

UNREACHABLE = _("We could not reach the QGIS account service, so nothing was changed.")
REFUSED = _("The QGIS account service refused the change, so nothing was changed.")


class RevocationError(Exception):
    """A revocation was refused, and why, in words for the reader."""


def grace() -> timedelta:
    return timedelta(
        days=getattr(settings, "SSO_REVOCATION_GRACE_DAYS", DEFAULT_GRACE_DAYS)
    )


@dataclass
class Blast:
    """What a revocation would do, before it does any of it."""

    target: KeycloakIdentity
    descendants: list[KeycloakIdentity] = field(default_factory=list)

    @property
    def total(self) -> int:
        return 1 + len(self.descendants)


def subtree(identity: KeycloakIdentity) -> list[KeycloakIdentity]:
    """Every identity below this one, breadth first.

    A cycle should be impossible, but the walk still stops at ``MAX_DEPTH`` and
    never visits an identity twice.
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


def ancestors(identity: KeycloakIdentity) -> list[KeycloakIdentity]:
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


def ancestor_user_ids(identity_ids: list[int]) -> dict[int, set[int]]:
    """The users above each of these identities, for a whole page in one query.

    Returns ``{identity_id: {user_id, ...}}``. The recursion stops at
    ``MAX_DEPTH``, like :func:`ancestors`.
    """
    identity_ids = list(identity_ids)
    if not identity_ids:
        return {}

    table = KeycloakIdentity._meta.db_table
    statement = f"""
        WITH RECURSIVE chain(root_id, user_id, sponsor_id, depth) AS (
            SELECT node.id, node.user_id, node.sponsor_id, 0
            FROM {table} AS node
            WHERE node.id = ANY(%s)
          UNION ALL
            SELECT chain.root_id, parent.user_id, parent.sponsor_id, chain.depth + 1
            FROM chain
            JOIN {table} AS parent ON parent.user_id = chain.sponsor_id
            WHERE chain.depth < %s
        )
        SELECT root_id, user_id FROM chain WHERE depth > 0
    """

    found = defaultdict(set)
    with connection.cursor() as cursor:
        cursor.execute(statement, [identity_ids, MAX_DEPTH])
        for root_id, user_id in cursor.fetchall():
            found[root_id].add(user_id)
    return dict(found)


def refusal(
    actor: User,
    identity: KeycloakIdentity,
    actor_ancestors: set[int] | None = None,
    subject_ancestors: set[int] | None = None,
) -> str | None:
    """Why ``actor`` may not withdraw trust from ``identity``, or None.

    An inviter reaches their own subtree and an administrator the whole forest.
    Neither reaches upwards, and neither reaches another administrator. A caller
    listing many rows can pass both ancestor sets from
    :func:`ancestor_user_ids`.
    """
    if identity.user_id == actor.pk:
        return _("You cannot withdraw trust from your own account.")

    actor_identity = getattr(actor, "keycloak_identity", None)
    if actor_identity is None or actor_identity.trust_state != TrustState.ACTIVE:
        return _("Your account cannot withdraw trust.")

    # Nothing has been trusted yet, and cancelling undoes the account cleanly.
    if identity.first_sso_login_at is None:
        return _(
            "%(username)s has not signed in yet, so there is no trust to "
            "withdraw. Cancel their invitation instead."
        ) % {"username": identity.user.username}

    if actor_ancestors is None:
        actor_ancestors = {node.user_id for node in ancestors(actor_identity)}
    if identity.user_id in actor_ancestors:
        return _(
            "You cannot withdraw trust from the person who invited you. "
            "Ask another administrator."
        )

    # Removing an administrator should need a second one to agree, which we
    # have not built, so the case is refused.
    if identity.is_root or tier_of(identity) == 0:
        return _(
            "You cannot withdraw trust from an administrator. Remove their "
            "administrator role first, or ask another administrator."
        )

    if out_of_reach(actor, identity, subject_ancestors):
        return _("You can only withdraw trust from people you invited.")
    return None


def out_of_reach(
    actor: User,
    identity: KeycloakIdentity,
    subject_ancestors: set[int] | None = None,
) -> bool:
    """Whether ``identity`` is outside what ``actor`` may act on at all.

    Shared by withdrawing and restoring. Restoring skips the other guards in
    :func:`refusal`, so a wrongly revoked administrator can still come back.
    """
    actor_identity = getattr(actor, "keycloak_identity", None)
    if actor_identity is None or actor_identity.trust_state != TrustState.ACTIVE:
        return True
    if actor.is_superuser and effective_tier(actor) == 0:
        return False
    if subject_ancestors is None:
        subject_ancestors = {node.user_id for node in ancestors(identity)}
    return actor.pk not in subject_ancestors


def may_revoke(
    actor: User,
    identity: KeycloakIdentity,
    actor_ancestors: set[int] | None = None,
    subject_ancestors: set[int] | None = None,
) -> bool:
    """Whether ``actor`` may withdraw trust from ``identity``."""
    return (
        refusal(
            actor,
            identity,
            actor_ancestors=actor_ancestors,
            subject_ancestors=subject_ancestors,
        )
        is None
    )


def preview(actor: User, identity: KeycloakIdentity) -> Blast:
    """Who a revocation would reach, by name, before anything happens.

    Raises :class:`RevocationError` when it is not allowed.
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
    # The ancestor rule should already prevent this. It is checked anyway,
    # because suspending yourself is the one outcome nobody could undo.
    if any(node.user_id == actor.pk for node in blast.descendants):
        raise RevocationError(
            _("This would suspend your own account, so it was not done.")
        )
    return blast


@transaction.atomic
def revoke(
    actor: User,
    identity: KeycloakIdentity,
    reason: str,
    client: KeycloakAdminClient | None = None,
) -> Blast:
    """Withdraw trust, and suspend everyone below.

    The realm changes come last in the transaction. If they fail, or anything
    local fails before them, nothing is changed in either place.
    """
    blast = preview(actor, identity)
    reason = (reason or "").strip()
    if not reason:
        raise RevocationError(_("Give a reason. It goes in the audit trail."))

    roles = list(identity.last_seen_roles or [])
    now = timezone.now()
    identity.trust_state = TrustState.REVOKED
    identity.revoked_at = now
    identity.revoked_by = actor
    identity.revocation_reason = reason
    identity.roles_at_revocation = roles
    identity.revoked_directly = True
    # Cleared, or the stale roles would keep their tier and quota.
    identity.last_seen_roles = []
    identity.save(
        update_fields=[
            "trust_state",
            "revoked_at",
            "revoked_by",
            "revocation_reason",
            "roles_at_revocation",
            "revoked_directly",
            "last_seen_roles",
        ]
    )
    # Now, because a revoked account never signs in again to be mirrored.
    mirror_roles(identity.user, [])

    identity.user.is_active = False
    # A fresh unusable password changes the session hash, which ends every
    # session this person has open on this site.
    identity.user.set_unusable_password()
    identity.user.save(update_fields=["is_active", "password"])

    for node in blast.descendants:
        node.trust_state = TrustState.SUSPENDED
        node.revoked_at = now
        node.revoked_by = actor
        node.revocation_reason = reason
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
        reason=reason,
        roles=roles,
        suspended=[node.user.username for node in blast.descendants],
    )

    run_hook(identity.user)
    withdraw_in_realm(identity, roles, client=client)
    return blast


def reactivate(
    identity: KeycloakIdentity, revoked_at: datetime | None
) -> list[KeycloakIdentity]:
    """Bring a branch back from suspension, and say who came with it.

    ``identity`` always comes back. Below it, only accounts suspended by the
    same revocation do, which ``revoked_at`` identifies. Purely local, because
    the realm never knew about the suspension.
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
        # Now, or a restored account sees nothing until its next sign-in.
        mirror_roles(node.user, node.last_seen_roles)
        brought_back.append(node)
    return brought_back


def restore(
    actor: User,
    identity: KeycloakIdentity,
    client: KeycloakAdminClient | None = None,
) -> None:
    """Give back what a revocation took, with the subtree it suspended.

    Only inside the grace window. Accounts suspended for some other reason stay
    suspended. The realm changes come last, as in :func:`revoke`.
    """
    if identity.trust_state != TrustState.REVOKED:
        raise RevocationError(_("That account has not been revoked."))
    if identity.user_id == actor.pk or out_of_reach(actor, identity):
        raise RevocationError(_("You cannot restore that account."))
    if not within_grace(identity):
        raise RevocationError(_("This revocation is too old to undo in one step."))

    roles = list(identity.roles_at_revocation or [])
    with transaction.atomic():
        reactivate(identity, identity.revoked_at)

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

        restore_in_realm(identity, roles, client=client)


def within_grace(identity: KeycloakIdentity) -> bool:
    """Whether a revocation is recent enough to undo in one action."""
    return bool(identity.revoked_at and timezone.now() - identity.revoked_at <= grace())


def withdraw_in_realm(
    identity: KeycloakIdentity,
    roles: list[str],
    client: KeycloakAdminClient | None = None,
) -> None:
    """Remove the roles, end the sessions and disable the account, in that order.

    Roles alone leave a session working until its token expires. Disabling
    alone leaves the roles to come back if the account is enabled again.
    """
    realm, uuid = _session(client)
    try:
        if roles:
            realm.remove_client_roles(identity.sub, uuid, _known(realm, uuid, roles))
        realm.end_sessions(identity.sub)
        realm.set_user_enabled(identity.sub, False)
    except KeycloakError:
        logger.exception("The realm refused to withdraw trust from %s", identity.sub)
        raise RevocationError(REFUSED)


def restore_in_realm(
    identity: KeycloakIdentity,
    roles: list[str],
    client: KeycloakAdminClient | None = None,
) -> None:
    realm, uuid = _session(client)
    try:
        realm.set_user_enabled(identity.sub, True)
        if roles:
            realm.assign_client_roles(identity.sub, uuid, _known(realm, uuid, roles))
    except KeycloakError:
        logger.exception("The realm refused to restore %s", identity.sub)
        raise RevocationError(REFUSED)


def _session(client: KeycloakAdminClient | None) -> tuple[KeycloakAdminClient, str]:
    """The realm client and this site's client UUID.

    Raises :class:`RevocationError` when the realm cannot be reached.
    """
    try:
        session = client or KeycloakAdminClient()
        uuid = session.client_uuid(feed_client_id())
    except KeycloakError:
        logger.exception("Could not reach the realm to change trust")
        raise RevocationError(UNREACHABLE)
    return session, uuid


def _known(session: KeycloakAdminClient, uuid: str, names: list[str]) -> list[dict]:
    """Role representations for the names we hold, skipping any that vanished."""
    catalogue = session.client_roles(uuid)
    return [catalogue[name] for name in names if name in catalogue]


def run_hook(user: User) -> None:
    """Let the site decide what happens to the person's unpublished work.

    ``SSO_ON_REVOKE`` names the callable, so ``qgis_sso`` knows nothing about
    entries or reviews.
    """
    path = getattr(settings, "SSO_ON_REVOKE", "")
    if not path:
        return
    import_string(path)(user)
