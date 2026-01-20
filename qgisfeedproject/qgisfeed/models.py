# coding=utf-8
""" "QGIS News Feed Entry model definition

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""

__author__ = "elpaso@itopen.it"
__date__ = "2019-05-07"
__copyright__ = "Copyright 2019, ItOpen"

from django.contrib.auth import get_user_model
from django.contrib.gis.db import models
from django.core.exceptions import ValidationError
from django.db.models import Count, F, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFill
from qgisfeed.utils import simplify
from user_visit.models import UserVisit

from .languages import LANGUAGES


class QgisLanguageField(models.CharField):
    """
    A language field for Django models.
    """

    def __init__(self, *args, **kwargs):
        # Local import so the languages aren't loaded unless they are needed.
        kwargs.setdefault("max_length", 3)
        kwargs.setdefault("choices", LANGUAGES)
        super().__init__(*args, **kwargs)


class PublishedManager(models.Manager):
    """Returns published entries, considering the publication
    dates and the published flag"""

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                Q(publish_from__isnull=True) | (Q(publish_from__lte=timezone.now())),
                Q(publish_to__isnull=True) | (Q(publish_to__gte=timezone.now())),
                published=True,
            )
        )


class CharacterLimitConfiguration(models.Model):
    """
    Set a hard character limit of a field
    """

    field_name = models.CharField(max_length=255, unique=True)
    max_characters = models.PositiveIntegerField()

    def __str__(self):
        return self.field_name


class ConfigurableCharField(models.CharField):
    """
    Customized CharField: the characters limit depends on the configuration
    """

    def __init__(self, *args, **kwargs):
        field_name = kwargs.pop("field_name", None)
        super(ConfigurableCharField, self).__init__(*args, **kwargs)
        if field_name:
            try:
                config = CharacterLimitConfiguration.objects.get(field_name=field_name)
                self.max_length = config.max_characters
            except CharacterLimitConfiguration.DoesNotExist:
                pass


class QgisFeedEntry(models.Model):
    """A feed entry for QGIS welcome page"""

    # Status choices
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    PUBLISHED = "published"

    STATUS_CHOICES = [
        (DRAFT, _("Draft")),
        (PENDING_REVIEW, _("Pending Review")),
        (CHANGES_REQUESTED, _("Changes Requested")),
        (APPROVED, _("Approved")),
        (PUBLISHED, _("Published")),
    ]

    title = models.CharField(_("Title"), max_length=255)
    image = ProcessedImageField(
        [ResizeToFill(500, 354)],
        "JPEG",
        {"quality": 60},
        _("Image"),
        upload_to="feedimages/%Y/%m/%d/",
        height_field="image_height",
        width_field="image_width",
        max_length=None,
        blank=True,
        null=True,
        help_text=_(
            "Landscape orientation, image will be cropped and scaled automatically to 500x354 px"
        ),
    )
    content = models.TextField()
    url = models.URLField(
        _("URL"),
        max_length=200,
        help_text=_("URL for more information link"),
        blank=True,
        null=True,
    )

    # Auto fields
    author = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        editable=False,
        related_name="authored_entries",
    )
    image_height = models.IntegerField(
        _("Image height"), blank=True, null=True, editable=False
    )
    image_width = models.IntegerField(
        _("Image width"), blank=True, null=True, editable=False
    )
    created = models.DateTimeField(
        _("Creation date"), auto_now=False, auto_now_add=True, editable=False
    )
    modified = models.DateTimeField(
        _("Modification date"), auto_now=True, editable=False, db_index=True
    )

    # Status and review tracking
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=STATUS_CHOICES,
        default=DRAFT,
        db_index=True,
        help_text=_("Current workflow status of the entry"),
    )

    assigned_reviewer = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewing_entries",
        verbose_name=_("Assigned Reviewer"),
        help_text=_("Primary reviewer assigned to this entry"),
    )

    # Options
    published = models.BooleanField(_("Published"), default=False, db_index=True)
    sticky = models.BooleanField(
        _("Sticky entry"),
        default=False,
        help_text=_("Check this option to keep this entry on top"),
    )
    sorting = models.PositiveIntegerField(
        blank=False,
        null=False,
        default=0,
        verbose_name=_("Sorting order"),
        help_text=_("Increase to show at top of the list"),
        db_index=True,
    )

    # Filters
    language_filter = QgisLanguageField(
        _("Language filter"),
        blank=True,
        null=True,
        help_text=_(
            "The entry will be hidden to users who have not set a matching language filter"
        ),
        db_index=True,
    )
    spatial_filter = models.PolygonField(
        _("Spatial filter"),
        blank=True,
        null=True,
        help_text=_(
            "The entry will be hidden to users who have set a location that does not match"
        ),
    )

    # Dates
    publish_from = models.DateTimeField(
        _("Publication start (UTC)"),
        auto_now=False,
        auto_now_add=False,
        blank=True,
        null=True,
        db_index=True,
    )
    publish_to = models.DateTimeField(
        _("Publication end (UTC)"),
        auto_now=False,
        auto_now_add=False,
        blank=True,
        null=True,
        db_index=True,
        help_text=_(
            "The entry will be hidden to users after this date. You can set this in the past to temove the entry from the users listing."
        ),
    )

    # Managers
    objects = models.Manager()
    published_entries = PublishedManager()

    @property
    def publish_from_epoch(self):
        """Return publish_from as epoch"""

        if self.publish_from is not None:
            return self.publish_from.timestamp()
        return 0

    def __str__(self):
        return self.title

    @property
    def language_filter_text(self):
        """Return the text value of the language filter"""
        if self.language_filter:
            choices_dict = dict(LANGUAGES)
            return choices_dict.get(self.language_filter, "English")
        return None

    class Meta:
        db_table = ""
        managed = True
        verbose_name = _("QGIS Feed Entry")
        verbose_name_plural = _("QGIS Feed Entries")
        ordering = ("-sticky", "-sorting", "-publish_from")

    def set_request(self, request):
        self._request = request

    def save(self, *args, **kwargs):
        """Auto-set author and notify superadmin when entry is added"""
        if self.pk is None and hasattr(self, "_request") and self._request:
            self.author = self._request.user

        # Auto-set published flag based on status
        if self.status == self.PUBLISHED:
            self.published = True
            if self.publish_from is None:
                self.publish_from = timezone.now()
        else:
            # If status is not PUBLISHED, ensure published flag is False
            self.published = False

        try:
            config = CharacterLimitConfiguration.objects.get(field_name="content")
            content_max_length = config.max_characters
        except CharacterLimitConfiguration.DoesNotExist:
            content_max_length = 500

        if len(self.content) > content_max_length:
            raise ValidationError(
                f"Ensure content value has at most {str(content_max_length)} characters (it has {str(len(self.content))})."
            )

        super(QgisFeedEntry, self).save(*args, **kwargs)


class FeedEntryReview(models.Model):
    """Review comments and actions for feed entries"""

    # Action choices
    ACTION_APPROVE = "approve"
    ACTION_REQUEST_CHANGES = "request_changes"
    ACTION_COMMENT = "comment"

    ACTION_CHOICES = [
        (ACTION_APPROVE, _("Approve")),
        (ACTION_REQUEST_CHANGES, _("Request Changes")),
        (ACTION_COMMENT, _("Comment")),
    ]

    entry = models.ForeignKey(
        QgisFeedEntry,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("Feed Entry"),
    )
    reviewer = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, verbose_name=_("Reviewer")
    )
    comment = models.TextField(_("Comment"), help_text=_("Review feedback or comments"))
    action = models.CharField(
        _("Action"),
        max_length=20,
        choices=ACTION_CHOICES,
        help_text=_("Review action taken"),
    )
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Feed Entry Review")
        verbose_name_plural = _("Feed Entry Reviews")

    def __str__(self):
        return f"{self.reviewer.username} - {self.get_action_display()} - {self.entry.title}"


class FeedEntryRevision(models.Model):
    """Track changes to feed entries for audit trail"""

    entry = models.ForeignKey(
        QgisFeedEntry,
        on_delete=models.CASCADE,
        related_name="revisions",
        verbose_name=_("Feed Entry"),
    )
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, verbose_name=_("Modified by")
    )

    # Snapshot of changed fields
    title = models.CharField(_("Title"), max_length=255)
    content = models.TextField(_("Content"))
    url = models.URLField(_("URL"), max_length=200, blank=True, null=True)

    # Metadata
    changed_at = models.DateTimeField(_("Changed at"), auto_now_add=True)
    change_summary = models.CharField(
        _("Change Summary"),
        max_length=255,
        blank=True,
        help_text=_("Brief description of what changed"),
    )

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = _("Feed Entry Revision")
        verbose_name_plural = _("Feed Entry Revisions")

    def __str__(self):
        return f"{self.entry.title} - {self.changed_at.strftime('%Y-%m-%d %H:%M')}"


class QgisUserVisit(models.Model):

    user_visit = models.OneToOneField(
        UserVisit, on_delete=models.CASCADE, primary_key=True
    )

    location = models.JSONField()

    # Mozilla/5.0 QGIS/32400/Fedora Linux (Workstation Edition)
    qgis_version = models.CharField(max_length=255, default="", blank=True)

    platform = models.CharField(max_length=255, default="", blank=True)


class DailyQgisUserVisit(models.Model):

    date = models.DateField(blank=True)

    qgis_version = models.JSONField()

    platform = models.JSONField()

    country = models.JSONField()


def aggregate_user_visit_data():
    user_visits = QgisUserVisit.objects.all()

    # Group by date
    user_visit_dates = (
        user_visits.annotate(date=F("user_visit__timestamp__date"))
        .values_list("date", flat=True)
        .distinct()
    )

    for user_visit_date in user_visit_dates:
        daily_visit, _ = DailyQgisUserVisit.objects.get_or_create(
            date=user_visit_date,
            defaults={"qgis_version": {}, "platform": {}, "country": {}},
        )

        qgis_user_visit = user_visits.filter(
            user_visit__timestamp__date=user_visit_date
        )

        total_platform_data = dict(
            qgis_user_visit.values("platform")
            .annotate(total_platform=Count("platform"))
            .values_list("platform", "total_platform")
        )

        total_country = dict(
            qgis_user_visit.filter(location__country_code__isnull=False)
            .values("location__country_code")
            .annotate(total_country=Count("location__country_code"))
            .values_list("location__country_code", "total_country")
        )

        total_qgis_version = dict(
            qgis_user_visit.exclude(qgis_version="")
            .values("qgis_version")
            .annotate(total_qgis_version=Count("qgis_version"))
            .values_list("qgis_version", "total_qgis_version")
        )

        if total_platform_data:
            daily_platform_data = daily_visit.platform
            for platform, value in total_platform_data.items():
                platform = simplify(platform)
                if platform not in daily_platform_data:
                    daily_platform_data[platform] = value
                else:
                    daily_platform_data[platform] += value
            daily_visit.platform = daily_platform_data

        if total_country:
            daily_country = daily_visit.country
            for country, value in total_country.items():
                if country not in daily_country:
                    daily_country[country] = value
                else:
                    daily_country[country] += value
            daily_visit.country = daily_country

        if total_qgis_version:
            daily_qgis_version = daily_visit.qgis_version
            for qgis_version, value in total_qgis_version.items():
                qgis_version = simplify(qgis_version)
                if qgis_version not in daily_qgis_version:
                    daily_qgis_version[qgis_version] = value
                else:
                    daily_qgis_version[qgis_version] += value
            daily_visit.qgis_version = daily_qgis_version

        daily_visit.save()

        # We need to delete entries from QgisUserVisit first
        qgis_user_visit.delete()

        UserVisit.objects.filter(timestamp__date=user_visit_date).delete()
