# coding=utf-8
"""Role names, and the ladder that decides who may invite whom.

The tier decides invitations and nothing else. Permissions still come from the
mirrored Django groups. ``SSO_ROLE_TIERS`` and ``SSO_TIER_QUOTAS`` hold this
site's ladder, so another site declares its own.
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import QuerySet

from .models import KeycloakIdentity, TrustState

#: The tier of an account with no known role. It sits below every real tier.
NO_TIER = 99


def ladder() -> dict[str, int]:
    return getattr(settings, "SSO_ROLE_TIERS", {})


def quotas() -> dict[int, int | None]:
    return getattr(settings, "SSO_TIER_QUOTAS", {})


def label(role: str) -> str:
    """The on-screen name of a role, or its identifier if it has none."""
    return getattr(settings, "SSO_ROLE_LABELS", {}).get(role, role)


def labels(roles: list[str]) -> list[str]:
    return [label(role) for role in roles]


def summary(role: str) -> str:
    """One line on what the role lets a person do, or an empty string."""
    return getattr(settings, "SSO_ROLE_SUMMARIES", {}).get(role, "")


def roles_of(user: User) -> list[str]:
    """The roles this site knows, as of the account's last sign-in.

    Read from the identity, because an administrator and a web maintainer share
    the same Django groups but sit a tier apart.
    """
    identity = getattr(user, "keycloak_identity", None)
    if identity is None:
        return []
    # A revoked or suspended account holds no tier. Its saved roles are stale.
    if not identity.trusted:
        return []
    known = ladder()
    return [role for role in identity.last_seen_roles if role in known]


def effective_tier(user: User) -> int:
    """The most privileged tier among the roles held. Lower is more trusted."""
    tiers = [ladder()[role] for role in roles_of(user)]
    return min(tiers, default=NO_TIER)


def tier_of(identity: KeycloakIdentity) -> int:
    """The tier an identity holds, whether or not it is trusted right now.

    ``effective_tier`` gives a suspended account no tier, which is right for
    quotas. A suspended administrator still outranks a reviewer, though. The
    roles are cleared on revocation, so this falls back to the ones kept then.
    """
    roles = list(identity.last_seen_roles or []) or list(
        identity.roles_at_revocation or []
    )
    known = ladder()
    return min((known[role] for role in roles if role in known), default=NO_TIER)


def quota(user: User) -> int | None:
    """How many invitations this account may have open at once. None is unlimited.

    It is the largest allowance among the roles held, never their sum.
    """
    tier = effective_tier(user)
    if tier == NO_TIER:
        return 0
    allowances = [quotas().get(ladder()[role], 0) for role in roles_of(user)]
    if any(allowance is None for allowance in allowances):
        return None
    return max(allowances, default=0)


def invitable_roles(user: User) -> list[str]:
    """The roles this account may offer, most privileged first.

    Never above the inviter's own tier, so nobody can climb the graph.
    """
    tier = effective_tier(user)
    if tier == NO_TIER:
        return []
    return sorted(
        (role for role, level in ladder().items() if level >= tier),
        key=lambda role: ladder()[role],
    )


def open_invitations(user: User) -> QuerySet[KeycloakIdentity]:
    """The accounts this person vouched for that nobody has signed in to yet.

    A place frees up when that person signs in, when the sponsor cancels, or
    when trust in the account is withdrawn.
    """
    return KeycloakIdentity.objects.filter(
        sponsor=user,
        first_sso_login_at=None,
        trust_state=TrustState.ACTIVE,
    )


def remaining(user: User) -> int | None:
    """How many more invitations this person may open now. None is unlimited."""
    allowance = quota(user)
    if allowance is None:
        return None
    return max(0, allowance - open_invitations(user).count())


def may_invite(user: User, role: str) -> bool:
    """Whether this account may offer that role, as of its last sign-in."""
    return role in invitable_roles(user)
