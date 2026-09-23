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


def label(role):
    """What to call that role on screen.

    Falls back to the identifier, so a role the site has not named still shows
    something rather than nothing.
    """
    return getattr(settings, "SSO_ROLE_LABELS", {}).get(role, role)


def labels(roles):
    return [label(role) for role in roles]


def summary(role):
    """One line on what the role lets a person do. Empty if unsaid."""
    return getattr(settings, "SSO_ROLE_SUMMARIES", {}).get(role, "")


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


def tier_of(identity):
    """The tier an identity holds, read from the record rather than the user.

    ``effective_tier`` answers for a *trusted* account and returns ``NO_TIER``
    for anybody suspended or revoked, which is the right answer for quotas and
    the wrong one for authority. A suspended administrator is still an
    administrator, and asking whether somebody outranks them has to say so.

    ``last_seen_roles`` is cleared on revocation, so fall back to the roles
    kept at that moment.
    """
    roles = list(identity.last_seen_roles or []) or list(
        identity.roles_at_revocation or []
    )
    known = ladder()
    return min((known[role] for role in roles if role in known), default=NO_TIER)


def quota(user):
    """How many invitations this account may have open at one time.

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


def open_invitations(user):
    """The places this person is still holding for somebody.

    An outstanding invitation, read from the graph rather than from a second
    table: an account whose holder has not turned up yet is a place still
    being held for them.

    Three things end that. They sign in, so the place has done its job. The
    sponsor frees it by hand, because the person is never coming. Or trust in
    the account is withdrawn, which leaves a place nobody could ever use.
    """
    from .models import KeycloakIdentity, TrustState

    return KeycloakIdentity.objects.filter(
        sponsor=user,
        first_sso_login_at=None,
        invitation_released_at=None,
        trust_state=TrustState.ACTIVE,
    )


def remaining(user):
    """How many more accounts this person may create. ``None`` is unlimited.

    A limit on invitations open at once, not on how many somebody may ever
    send: anybody may invite as many people as they need, as long as they are
    not all waiting at the same time.
    """
    allowance = quota(user)
    if allowance is None:
        return None
    return max(0, allowance - open_invitations(user).count())


def may_invite(user, role):
    """Whether this account may offer that role, re-checked at every use.

    Called on issue and again on redemption: an invitation from somebody since
    demoted must grant no more than they can grant now (US-8.2).
    """
    return role in invitable_roles(user)
