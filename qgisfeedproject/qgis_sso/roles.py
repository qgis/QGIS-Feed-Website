# coding=utf-8
"""Mirror Keycloak client roles onto Django groups and staff flags.

Keycloak is the only place roles are edited. Django's groups and the
``is_staff`` / ``is_superuser`` flags are a projection of them, recomputed in
full on every login, so that revoking a role in Keycloak revokes it here too.
A mirror that only ever adds is not a mirror; it is an accumulator.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


def managed_groups():
    """Names of the groups this app is allowed to add to and remove from.

    Anything outside this set is left alone, so a group created by hand for an
    unrelated purpose is not clobbered by a login.
    """
    return set(getattr(settings, "SSO_MANAGED_GROUPS", []))


def target_state(roles):
    """Reduce a list of Keycloak role names to the Django state they imply.

    Returns ``(group_names, is_staff, is_superuser)``. Unknown roles are
    ignored rather than rejected: the realm is shared with other QGIS sites,
    and a token may legitimately carry roles that mean nothing here.

    An empty or absent role list yields no groups and no flags, which is the
    correct outcome both for a revoked contributor and for a hub or plugins
    user who wanders in.
    """
    role_map = getattr(settings, "SSO_ROLE_MAP", {})
    groups = set()
    is_staff = False
    is_superuser = False

    for role in roles or []:
        spec = role_map.get(role)
        if spec is None:
            logger.debug("Ignoring role %r: not in SSO_ROLE_MAP", role)
            continue
        groups.update(spec.get("groups", []))
        is_staff = is_staff or bool(spec.get("is_staff", False))
        is_superuser = is_superuser or bool(spec.get("is_superuser", False))

    # A role map naming a group that is not managed would grant something the
    # mirror could never take back. Constrain rather than trust.
    unmanaged = groups - managed_groups()
    if unmanaged:
        logger.warning(
            "SSO_ROLE_MAP grants groups missing from SSO_MANAGED_GROUPS: %s. "
            "They will not be granted, because they could never be revoked.",
            ", ".join(sorted(unmanaged)),
        )
    return groups & managed_groups(), is_staff, is_superuser


def mirror_roles(user, roles):
    """Reconcile ``user`` to exactly the state ``roles`` implies.

    Returns a dict describing what changed, for the audit record.

    Ordering matters. The flags are saved *before* the groups are reconciled,
    because saving a user fires ``qgisfeed.signals.setup_group``; doing it the
    other way round would let that receiver re-add a group this call had just
    removed. The receiver additionally exempts SSO-linked accounts, which is
    what makes the outcome stable against the ``last_login`` save that
    ``django.contrib.auth.login`` performs after authentication returns.
    """
    groups, is_staff, is_superuser = target_state(roles)

    changed = {"roles": sorted(roles or [])}

    if user.is_staff != is_staff or user.is_superuser != is_superuser:
        changed["is_staff"] = [user.is_staff, is_staff]
        changed["is_superuser"] = [user.is_superuser, is_superuser]
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.save(update_fields=["is_staff", "is_superuser"])

    managed = managed_groups()
    if managed:
        current = set(
            user.groups.filter(name__in=managed).values_list("name", flat=True)
        )
        to_add = groups - current
        to_remove = current - groups

        if to_add:
            # Only groups that already exist are added. The groups this site
            # manages are created by qgisfeed's own post-migrate receivers;
            # inventing one here would paper over a misconfigured role map.
            found = list(Group.objects.filter(name__in=to_add))
            missing = to_add - {group.name for group in found}
            if missing:
                logger.error(
                    "SSO_ROLE_MAP references groups that do not exist: %s",
                    ", ".join(sorted(missing)),
                )
            if found:
                user.groups.add(*found)
            changed["groups_added"] = sorted({group.name for group in found})
        if to_remove:
            user.groups.remove(*Group.objects.filter(name__in=to_remove))
            changed["groups_removed"] = sorted(to_remove)

    return changed
