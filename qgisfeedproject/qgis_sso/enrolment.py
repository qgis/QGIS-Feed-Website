# coding=utf-8
"""Who can publish, read from the local database for a whole batch at once."""

from django.contrib.auth.models import User
from django.db.models import Q

#: The permission that makes somebody a reviewer in Keycloak. Matched on the
#: app label too, so a same-named permission in another app cannot grant it.
PUBLISH_APP = "qgisfeed"
PUBLISH_PERMISSION = "publish_qgisfeedentry"


def publishers():
    """Who holds ``publish_qgisfeedentry``, in one query.

    ``user.has_perm`` costs two queries per account. Inactive accounts are left
    out, because ``ModelBackend`` grants them nothing.
    """
    holders = User.objects.filter(
        Q(
            user_permissions__codename=PUBLISH_PERMISSION,
            user_permissions__content_type__app_label=PUBLISH_APP,
        )
        | Q(
            groups__permissions__codename=PUBLISH_PERMISSION,
            groups__permissions__content_type__app_label=PUBLISH_APP,
        ),
        is_active=True,
    )
    return set(holders.values_list("pk", flat=True))
