# coding=utf-8
"""What the feed does when somebody's trust is withdrawn.

``SSO_ON_REVOKE`` names this function, so ``qgis_sso`` knows nothing about
entries and can move to other QGIS sites unchanged.
"""

import logging

from django.contrib.auth.models import User

from .models import QgisFeedEntry

logger = logging.getLogger(__name__)

#: Entries a reviewer could publish without knowing the author was revoked.
UNPUBLISHED = (QgisFeedEntry.PENDING_REVIEW, QgisFeedEntry.APPROVED)


def on_revoke(user: User) -> int:
    """Return the revoked author's unpublished entries to draft.

    Nothing is deleted, and every entry keeps its author. Taking a published
    entry down is an editorial decision, so published entries stay.
    """
    affected = QgisFeedEntry.objects.filter(author=user, status__in=UNPUBLISHED)
    count = affected.update(status=QgisFeedEntry.DRAFT)
    if count:
        logger.warning(
            "Returned %d entr%s to draft: %s was revoked",
            count,
            "y" if count == 1 else "ies",
            user.username,
        )
    return count
