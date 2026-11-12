# coding=utf-8
"""Mirror Keycloak client roles onto Django groups and staff flags.

Roles are edited in Keycloak only. Groups, ``is_staff`` and ``is_superuser``
are rebuilt from them at every sign-in, so a role removed there is removed here.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import Group

logger = logging.getLogger(__name__)


def managed_groups() -> set[str]:
    """The groups mirroring may add or remove. Every other group is left alone."""
    return set(getattr(settings, "SSO_MANAGED_GROUPS", []))


def target_state(roles) -> tuple[set[str], bool, bool]:
    """Turn Keycloak role names into ``(groups, is_staff, is_superuser)``.

    Roles missing from ``SSO_ROLE_MAP`` are ignored, because the shared realm
    carries roles for other QGIS sites. No roles means no groups and no flags.
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

    # A group we do not manage could be granted but never taken back.
    managed = managed_groups()
    unmanaged = groups - managed
    if unmanaged:
        logger.warning(
            "SSO_ROLE_MAP grants groups missing from SSO_MANAGED_GROUPS: %s. "
            "They will not be granted, because they could never be revoked.",
            ", ".join(sorted(unmanaged)),
        )
    return groups & managed, is_staff, is_superuser


def mirror_roles(user, roles) -> dict[str, list]:
    """Set ``user`` to exactly what ``roles`` implies, and return what changed.

    The flags are saved before the groups, because saving a user fires
    ``qgisfeed.signals.setup_group``, which must not re-add a removed group.
    """
    groups, is_staff, is_superuser = target_state(roles)

    changed = {"roles": sorted(roles or [])}

    if user.is_staff != is_staff or user.is_superuser != is_superuser:
        changed["is_staff"] = [user.is_staff, is_staff]
        changed["is_superuser"] = [user.is_superuser, is_superuser]
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.save(update_fields=["is_staff", "is_superuser"])

    current = set(
        user.groups.filter(name__in=managed_groups()).values_list("name", flat=True)
    )
    to_add = groups - current
    to_remove = current - groups

    if to_add:
        # qgisfeed creates these groups. A missing one means a broken role map.
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
