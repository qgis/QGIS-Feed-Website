# coding=utf-8
import html
import logging
import unicodedata

from django.conf import settings
from django.contrib.gis.db.models import Model
from django.contrib.gis.geoip2 import GeoIP2
from django.core.mail import EmailMultiAlternatives
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import strip_tags

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


def notify_reviewers(author, request, recipients, obj):
    """Send notification emails to reviewers (no CC needed in new system)"""
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
    )
    msg.send(fail_silently=True)


def get_author_and_reviewer_recipients(entry):
    from django.contrib.auth.models import User

    recipients = set()

    if entry.author and entry.author.email:
        recipients.add(entry.author.email)

    if entry.reviewers.exists():
        for reviewer in entry.reviewers.all():
            if reviewer.email and reviewer.has_perm("qgisfeed.publish_qgisfeedentry"):
                recipients.add(reviewer.email)
    else:
        reviewers = User.objects.filter(is_active=True, email__isnull=False).exclude(
            email=""
        )
        recipients.update(
            [u.email for u in reviewers if u.has_perm("qgisfeed.publish_qgisfeedentry")]
        )

    return list(recipients)


def notify_entry_submitted(entry, submitted_by, request):
    recipients = get_author_and_reviewer_recipients(entry)
    if not recipients:
        return

    body = f"""
    Hi,

    {submitted_by.username} submitted the feed entry "{entry.title}" for review.

    Review it at:
    {request.build_absolute_uri(reverse('feed_entry_update', args=(entry.pk,)))}

    Your beloved QGIS Feed bot.
    """

    msg = EmailMultiAlternatives(
        f"Feed entry submitted for review: {entry.title}",
        body,
        DEFAULT_FROM_EMAIL,
        recipients,
    )
    msg.send(fail_silently=True)


def notify_review_action_submitted(entry, review, request):
    recipients = get_author_and_reviewer_recipients(entry)
    if not recipients:
        return

    action_display = review.get_action_display()
    body = f"""
    Hi,

    A new review action was submitted for "{entry.title}".

    Reviewer: {review.reviewer.username}
    Action: {action_display}
    Comment: {review.comment}

    View it at:
    {request.build_absolute_uri(reverse('feed_entry_update', args=(entry.pk,)))}

    Your beloved QGIS Feed bot.
    """

    msg = EmailMultiAlternatives(
        f"Feed entry review update ({action_display}): {entry.title}",
        body,
        DEFAULT_FROM_EMAIL,
        recipients,
    )
    msg.send(fail_silently=True)


def get_field_max_length(ConfigurationModel: Model, field_name: str):
    try:
        config = ConfigurationModel.objects.get(field_name=field_name)
        return config.max_characters
    except ConfigurationModel.DoesNotExist:
        return 500


def get_content_plain_text_length(content: str) -> int:
    if not content:
        return 0
    plain_text = html.unescape(strip_tags(content))
    return len(plain_text)


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
    - Author can edit if status is DRAFT, CHANGES_REQUESTED, PENDING_REVIEW, APPROVED, or PUBLISHED
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

    # Author can edit in multiple statuses, including PENDING_REVIEW
    if entry.author == user:
        return entry.status in [
            QgisFeedEntry.DRAFT,
            QgisFeedEntry.CHANGES_REQUESTED,
            QgisFeedEntry.PENDING_REVIEW,  # Authors can now edit while under review
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
    - Authors WITH publish permission CAN review their own entries
    """
    return user.has_perm("qgisfeed.publish_qgisfeedentry")


def can_publish_entry(user, entry):
    """
    Check if user can publish this entry.

    Rules:
    - User must have publish permission
    - Entry must be in APPROVED status
    - At least one reviewer must have approved (not all)
    """
    from qgisfeed.models import QgisFeedEntry

    if entry.status != QgisFeedEntry.APPROVED:
        return False

    # Only reviewers with publish permission can publish
    if user.has_perm("qgisfeed.publish_qgisfeedentry"):
        return True

    return False


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


def create_revision_snapshot(original_entry, new_instance, user):
    """Compare original_entry (old DB state) with new_instance (unsaved new state)
    and persist a FeedEntryRevision recording per-field old→new diffs.

    Returns the created FeedEntryRevision, or None when nothing changed.
    """
    from qgisfeed.models import FeedEntryRevision

    # (field_attr, human label, type hint)
    # type hints: 'text' | 'content' | 'file' | 'geometry' | 'datetime' | 'bool'
    TRACKED_FIELDS = [
        ("title", "Title", "text"),
        ("url", "URL", "text"),
        ("action_text", "Call to Action Text", "text"),
        ("content", "Content", "content"),
        ("image", "Image", "file"),
        ("sticky", "Sticky", "bool"),
        ("sorting", "Sorting Order", "text"),
        ("language_filter", "Language Filter", "text"),
        ("spatial_filter", "Spatial Filter", "geometry"),
        ("publish_from", "Publish From", "datetime"),
        ("publish_to", "Publish To", "datetime"),
    ]

    def _normalize_html(html_str):
        """Normalise HTML for comparison: collapse whitespace between/around tags
        so TinyMCE re-serialisation quirks (extra newlines, spaces) are ignored,
        while structural/formatting changes (bold, italic, etc.) are still detected.
        """
        import re

        if not html_str:
            return ""
        # Collapse runs of whitespace (spaces, tabs, newlines) into a single space
        normalised = re.sub(r"\s+", " ", html_str).strip()
        # Remove spaces immediately after an opening tag or before a closing tag
        normalised = re.sub(r">\s+", ">", normalised)
        normalised = re.sub(r"\s+<", "<", normalised)
        return normalised

    field_changes = []
    changed_labels = []

    for field_attr, label, field_type in TRACKED_FIELDS:
        old_val = getattr(original_entry, field_attr, None)
        new_val = getattr(new_instance, field_attr, None)

        # For content (HTML rich text) compare normalised HTML so that TinyMCE
        # whitespace re-serialisation quirks don't produce false positives,
        # but real formatting changes (bold, italic, etc.) are still detected.
        if field_type == "content":
            old_cmp = _normalize_html(old_val)
            new_cmp = _normalize_html(new_val)
        # Geometry comparison via WKT to avoid object identity issues
        elif field_type == "geometry":
            old_cmp = old_val.wkt if old_val else None
            new_cmp = new_val.wkt if new_val else None
        else:
            old_cmp, new_cmp = old_val, new_val

        if old_cmp == new_cmp:
            continue

        changed_labels.append(label)

        if field_type == "content":
            # Store the raw HTML so the template can render formatting
            field_changes.append(
                {"label": label, "old": old_val or None, "new": new_val or None}
            )
        elif field_type in ("file", "geometry"):
            # Too large / not human-readable — just record that it changed
            field_changes.append({"label": label, "old": None, "new": None})
        elif field_type == "datetime":
            field_changes.append(
                {
                    "label": label,
                    "old": old_val.strftime("%Y-%m-%d %H:%M") if old_val else None,
                    "new": new_val.strftime("%Y-%m-%d %H:%M") if new_val else None,
                }
            )
        elif field_type == "bool":
            field_changes.append(
                {
                    "label": label,
                    "old": "Yes" if old_val else "No",
                    "new": "Yes" if new_val else "No",
                }
            )
        else:
            field_changes.append(
                {
                    "label": label,
                    "old": str(old_val) if old_val is not None else None,
                    "new": str(new_val) if new_val is not None else None,
                }
            )

    if not field_changes:
        return None

    auto_summary = "Changed: " + ", ".join(changed_labels)

    return FeedEntryRevision.objects.create(
        entry=original_entry,
        user=user,
        title=new_instance.title,
        content=new_instance.content,
        url=new_instance.url,
        change_summary=auto_summary,
        field_changes=field_changes,
    )
