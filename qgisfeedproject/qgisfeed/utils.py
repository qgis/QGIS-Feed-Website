# coding=utf-8
import logging
import unicodedata

from django.conf import settings
from django.contrib.gis.db.models import Model
from django.contrib.gis.geoip2 import GeoIP2
from django.core.mail import EmailMultiAlternatives
from django.http import HttpRequest
from django.urls import reverse

logger = logging.getLogger("qgisfeed.admin")
DEFAULT_FROM_EMAIL = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@qgis.org")


def simplify(text: str) -> str:
    try:
        text = (
            unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("utf-8")
        )
    except:  # noqa
        pass
    return str(text)


def notify_reviewers(author, request, recipients, cc, obj):
    """Send notification emails"""
    body = f"""
        Hi, \r\n
        {author.username} asked you to review the feed entry available at {request.build_absolute_uri(reverse('feed_entry_update', args=(obj.pk,)))}
        Title: {obj.title}\r\n
        Your beloved QGIS Feed bot.
        """
    msg = EmailMultiAlternatives(
        "QGIS feed entry review requested by %s" % author.username,
        body,
        DEFAULT_FROM_EMAIL,
        recipients,
        cc=cc,
    )
    msg.send(fail_silently=True)


def get_field_max_length(ConfigurationModel: Model, field_name: str):
    try:
        config = ConfigurationModel.objects.get(field_name=field_name)
        return config.max_characters
    except ConfigurationModel.DoesNotExist:
        return 500


def parse_remote_addr(request: HttpRequest) -> str:
    """Extract client IP from request."""
    x_forwarded_for = request.headers.get("X-Forwarded-For", "")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0]
    return request.META.get("REMOTE_ADDR", "")


def get_location(remote_addr: str) -> str:
    """
    Return WKT location for the given remote_addr.
    This should be used only for the geofence feature
    and won't be saved in the database.
    """
    g = GeoIP2()
    if remote_addr:
        try:
            location = g.city(remote_addr)
            location_wkt = f"POINT({location['longitude']} {location['latitude']})"
            return location_wkt
        except Exception:
            return None
    return None


# Permission helper functions for feed entry workflow
def can_edit_entry(user, entry):
    """
    Check if user can edit this entry.

    Rules:
    - Author can edit if status is DRAFT, CHANGES_REQUESTED, APPROVED, or PUBLISHED
      (editing APPROVED/PUBLISHED entries will trigger re-review)
    - Reviewers (users with publish permission) can always edit
    - Superusers can always edit
    """
    from qgisfeed.models import QgisFeedEntry

    if user.is_superuser:
        return True

    # Reviewers can always edit
    if user.has_perm("qgisfeed.publish_qgisfeedentry"):
        return True

    # Author can edit in multiple statuses
    if entry.author == user:
        return entry.status in [
            QgisFeedEntry.DRAFT,
            QgisFeedEntry.CHANGES_REQUESTED,
            QgisFeedEntry.APPROVED,
            QgisFeedEntry.PUBLISHED,
        ]

    return False


def can_submit_for_review(user, entry):
    """
    Check if user can submit entry for review.

    Rules:
    - Only the author can submit
    - Entry must be in DRAFT or CHANGES_REQUESTED status
    """
    from qgisfeed.models import QgisFeedEntry

    return entry.author == user and entry.status in [
        QgisFeedEntry.DRAFT,
        QgisFeedEntry.CHANGES_REQUESTED,
    ]


def can_review_entry(user, entry):
    """
    Check if user can review this entry.

    Rules:
    - User must have publish permission
    - User cannot review their own entries
    """
    return user.has_perm("qgisfeed.publish_qgisfeedentry") and entry.author != user


def can_publish_entry(user, entry):
    """
    Check if user can publish this entry.

    Rules:
    - User must have publish permission
    - Entry must be in APPROVED status
    """
    from qgisfeed.models import QgisFeedEntry

    return (
        user.has_perm("qgisfeed.publish_qgisfeedentry")
        and entry.status == QgisFeedEntry.APPROVED
    )


# Extended notification functions for review workflow
def notify_author_changes_requested(entry, review, request):
    """Notify author that changes were requested"""
    if not entry.author.email:
        return

    body = f"""
    Hi {entry.author.username},

    Your feed entry "{entry.title}" needs some changes before it can be published.

    Reviewer: {review.reviewer.username}
    Comment: {review.comment}

    Please review the feedback and make the necessary changes at:
    {request.build_absolute_uri(reverse('feed_entry_update', args=(entry.pk,)))}

    Your beloved QGIS Feed bot.
    """

    msg = EmailMultiAlternatives(
        f"Changes requested for: {entry.title}",
        body,
        DEFAULT_FROM_EMAIL,
        [entry.author.email],
    )
    msg.send(fail_silently=True)


def notify_author_approved(entry, review, request):
    """Notify author that entry was approved"""
    if not entry.author.email:
        return

    body = f"""
    Hi {entry.author.username},

    Great news! Your feed entry "{entry.title}" has been approved and will be published soon.

    Reviewer: {review.reviewer.username}
    Comment: {review.comment}

    View your entry at:
    {request.build_absolute_uri(reverse('feed_entry_update', args=(entry.pk,)))}

    Your beloved QGIS Feed bot.
    """

    msg = EmailMultiAlternatives(
        f"Feed entry approved: {entry.title}",
        body,
        DEFAULT_FROM_EMAIL,
        [entry.author.email],
    )
    msg.send(fail_silently=True)


def notify_reviewers_resubmitted(entry, request):
    """Notify reviewers when author resubmits after making changes"""
    from django.contrib.auth.models import User

    reviewers = User.objects.filter(is_active=True, email__isnull=False).exclude(
        email=""
    )

    recipients = [
        u.email for u in reviewers if u.has_perm("qgisfeed.publish_qgisfeedentry")
    ]

    if not recipients:
        return

    body = f"""
    Hi,

    {entry.author.username} has resubmitted the feed entry "{entry.title}" after making requested changes.

    Please review the updated entry at:
    {request.build_absolute_uri(reverse('feed_entry_update', args=(entry.pk,)))}

    Your beloved QGIS Feed bot.
    """

    msg = EmailMultiAlternatives(
        f"Feed entry resubmitted for review: {entry.title}",
        body,
        DEFAULT_FROM_EMAIL,
        recipients,
    )
    msg.send(fail_silently=True)


def notify_author_published(entry, request):
    """Notify author that entry was published"""
    if not entry.author.email:
        return

    body = f"""
    Hi {entry.author.username},

    Your feed entry "{entry.title}" has been published and is now live!

    Users will start seeing it in their QGIS welcome page.

    View your entry at:
    {request.build_absolute_uri(reverse('feed_entry_update', args=(entry.pk,)))}

    Your beloved QGIS Feed bot.
    """

    msg = EmailMultiAlternatives(
        f"Feed entry published: {entry.title}",
        body,
        DEFAULT_FROM_EMAIL,
        [entry.author.email],
    )
    msg.send(fail_silently=True)


def create_revision_snapshot(entry, user, change_summary=""):
    """Create a revision snapshot of the entry"""
    from qgisfeed.models import FeedEntryRevision

    FeedEntryRevision.objects.create(
        entry=entry,
        user=user,
        title=entry.title,
        content=entry.content,
        url=entry.url,
        change_summary=change_summary,
    )
