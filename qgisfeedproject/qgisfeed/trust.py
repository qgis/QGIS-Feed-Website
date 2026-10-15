# coding=utf-8
"""What the feed does when somebody's trust is withdrawn.

``qgis_sso`` calls this and knows nothing about it: entries, statuses and
reviews are the feed's business, and the SSO app has to stay portable to other
QGIS sites (US-9.1). ``SSO_ON_REVOKE`` names this function.
"""

import logging

from .models import QgisFeedEntry

logger = logging.getLogger(__name__)

#: Waiting to be published, one way or another. These are the entries a
#: reviewer could push live without ever learning the author was revoked.
UNPUBLISHED = (QgisFeedEntry.PENDING_REVIEW, QgisFeedEntry.APPROVED)


def on_revoke(user):
    """Return the revoked author's unpublished entries to draft.

    US-5.1: content is **retained**, not deleted. Published entries are left
    alone - taking them down is an editorial decision, not an automatic
    consequence of an account being closed - and every entry keeps its author,
    so the history still says who wrote what.

    What this stops is the quiet case: an approved entry from somebody revoked
    an hour ago, published by a reviewer working through the queue who has no
    reason to know.
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
