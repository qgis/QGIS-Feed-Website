# coding=utf-8
"""Who may invite whom, and how many.

Tier governs invitation rights and nothing else. What somebody may do with the
content on this site comes from the mirrored Django permissions, exactly as it
did before there was a trust graph.

The ladder is configuration - ``SSO_ROLE_TIERS`` and ``SSO_TIER_QUOTAS``, whose
role names are the ones already in ``SSO_ROLE_MAP`` - so another site declares
its own without touching this module.
"""

from django.conf import settings

#: Returned for an account holding no role this site knows. Below every real
#: tier, so it can invite nobody and no comparison has to special-case None.
NO_TIER = 99


def ladder():
    return getattr(settings, "SSO_ROLE_TIERS", {})


def quotas():
    return getattr(settings, "SSO_TIER_QUOTAS", {})


def roles_of(user):
    """The role set as of that account's last sign-in.

    Read from the identity rather than from Django groups, because the groups
    cannot tell the difference: an administrator and a web maintainer are both
    superusers here, and they sit a tier apart.
    """
    identity = getattr(user, "keycloak_identity", None)
    if identity is None:
        return []
    if not identity.trusted:
        # Somebody revoked or suspended holds no tier, so no quota and nothing
        # to offer. Without this they keep inviting: the roles Keycloak knows
        # about are gone, but this field is the last thing a token said, and a
        # revoked account never signs in again to update it.
        return []
    known = ladder()
    return [role for role in identity.last_seen_roles if role in known]


def effective_tier(user):
    """The most privileged tier among the roles held.

    A person may hold several roles and the highest wins (US-2.4), so this is a
    minimum rather than a lookup of one role.
    """
    tiers = [ladder()[role] for role in roles_of(user)]
    return min(tiers, default=NO_TIER)


def quota(user):
    """How many invitations this account may have open at once.

    ``None`` is unlimited. The allowance is the *largest* of the roles held,
    not their total: holding two roles is not a reason to invite twice as many
    people as either role allows.
    """
    tier = effective_tier(user)
    if tier == NO_TIER:
        return 0
    allowances = [quotas().get(ladder()[role], 0) for role in roles_of(user)]
    if any(allowance is None for allowance in allowances):
        return None
    return max(allowances, default=0)


def invitable_roles(user):
    """The roles this account may offer, most privileged first.

    Never above the inviter's own tier - that is invariant 3, and the whole
    reason the graph cannot be climbed.
    """
    tier = effective_tier(user)
    if tier == NO_TIER:
        return []
    return sorted(
        (role for role, level in ladder().items() if level >= tier),
        key=lambda role: ladder()[role],
    )


def sponsored_not_yet_arrived(user):
    """Accounts this person created that have never signed in.

    The analogue of an outstanding invitation, read from the graph rather than
    from a second table: an account whose holder has not turned up yet is a
    place still being held for them.
    """
    from .models import KeycloakIdentity

    return KeycloakIdentity.objects.filter(sponsor=user, first_sso_login_at=None)


def remaining(user):
    """How many more accounts this person may create. ``None`` is unlimited."""
    allowance = quota(user)
    if allowance is None:
        return None
    return max(0, allowance - sponsored_not_yet_arrived(user).count())


def may_invite(user, role):
    """Whether this account may offer that role, re-checked at every use.

    Called on issue and again on redemption: an invitation from somebody since
    demoted must grant no more than they can grant now (US-8.2).
    """
    return role in invitable_roles(user)
