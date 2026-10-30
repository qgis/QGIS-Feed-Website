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


def setup_group(sender, **kwargs):
    """Create qgisfeedentry_authors group and assign permissions"""

    from django.contrib.auth.models import Group, Permission, User

    group, is_new = Group.objects.get_or_create(name="qgisfeedentry_authors")
    if is_new:
        for perm in ("view_qgisfeedentry", "add_qgisfeedentry"):
            group.permissions.add(Permission.objects.get(codename=perm))

    for staff_user in User.objects.filter(is_staff=True, is_superuser=False):
        group.user_set.add(staff_user)


def setup_approver_group(sender, **kwargs):
    """Create qgisfeedentry_approver group and assign permissions"""

    from django.contrib.auth.models import Group, Permission
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

    g = GeoIP2()
    country_data = {}
    qgis_version = ""
    platform_name = ""

    if instance.remote_addr:
        try:
            country_data = g.country(instance.remote_addr)
        except:  # AddressNotFoundErrors:
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
