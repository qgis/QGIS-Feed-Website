# coding=utf-8
""" "QGIS News app signals

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""

__author__ = "elpaso@itopen.it"
__date__ = "2019-05-08"
__copyright__ = "Copyright 2019, ItOpen"


def setup_group(sender, instance=None, **kwargs):
    """Create qgisfeedentry_authors group and assign permissions

    Staff users are added to the group, with two deliberate narrowings against
    the original behaviour, both needed once Keycloak roles are mirrored onto
    Django groups at every sign-in (see qgis_sso.roles.mirror_roles):

    * Only the user being saved is considered. Sweeping every staff user on
      each save meant that *any* user save - including the last_login write
      that django.contrib.auth.login performs after authentication - re-added
      every staff user to the group, undoing a revocation the mirror had just
      applied to somebody else. Every path that grants staff saves that user,
      so this receiver still fires for them.
    * SSO-linked accounts are left alone. Their group membership belongs to
      Keycloak; anything granted here would be revoked at their next sign-in,
      or would silently re-grant what the mirror had just taken away.
    """

    from django.contrib.auth.models import Group, Permission

    group, is_new = Group.objects.get_or_create(name="qgisfeedentry_authors")
    if is_new:
        for perm in ("view_qgisfeedentry", "add_qgisfeedentry"):
            group.permissions.add(Permission.objects.get(codename=perm))

    if instance is None or not instance.is_staff or instance.is_superuser:
        return

    from qgis_sso.models import KeycloakIdentity

    if KeycloakIdentity.objects.filter(user=instance).exists():
        return

    group.user_set.add(instance)


def setup_approver_group(sender, **kwargs):
    """Create qgisfeedentry_approver group and assign permissions"""

    from django.contrib.auth.models import Group, Permission, User
    from django.contrib.contenttypes.models import ContentType

    permission, created = Permission.objects.get_or_create(
        name="Can publish QGIS Feed Entry",
        content_type=ContentType.objects.get(model="qgisfeedentry"),
        codename="publish_qgisfeedentry",
    )
    group, is_new = Group.objects.get_or_create(name="qgisfeedentry_approver")
    if is_new:
        for perm in (
            "view_qgisfeedentry",
            "add_qgisfeedentry",
            "publish_qgisfeedentry",
            "change_qgisfeedentry",
        ):
            group.permissions.add(Permission.objects.get(codename=perm))


# Post save user visit signals
def post_save_user_visit(sender, instance, **kwargs):
    import re

    from django.contrib.gis.geoip2 import GeoIP2
    from qgisfeed.models import QgisUserVisit
    from user_visit.models import UserVisit

    country_data = {}
    qgis_version = ""
    platform_name = ""

    if instance.remote_addr:
        # Constructing GeoIP2 is itself a failure point: it raises when the
        # database is missing, so it belongs inside the guard. A visit is worth
        # recording without a location.
        try:
            country_data = GeoIP2().country(instance.remote_addr)
        except Exception:
            country_data = {}

    version_match = re.search("QGIS(.*)", instance.ua_string)

    if version_match:
        version_match_array = version_match.group().split("/")
        if len(version_match_array) > 1:
            qgis_version = version_match_array[1].strip()
        if len(version_match_array) > 2:
            platform_name = version_match_array[2].strip()

    if not platform_name:
        if instance.user_agent:
            platform_name = instance.user_agent.get_os()

    QgisUserVisit.objects.get_or_create(
        user_visit=instance,
        location=country_data,
        qgis_version=qgis_version,
        platform=platform_name,
    )

    UserVisit.objects.filter(pk=instance.pk).update(remote_addr="")
