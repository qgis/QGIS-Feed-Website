# coding=utf-8
""""QGIS News Feed Entry model definition

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""

__author__ = 'elpaso@itopen.it'
__date__ = '2019-05-07'
__copyright__ = 'Copyright 2019, ItOpen'

from django.contrib.auth import get_user_model
from django.contrib.gis.db import models
from django.db.models import Q, F, Count
from django.utils import timezone
from django.utils.translation import gettext as _

from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFill
from user_visit.models import UserVisit


class QgisLanguageField(models.CharField):
    """
    A language field for Django models.
    """
    def __init__(self, *args, **kwargs):
        # Local import so the languages aren't loaded unless they are needed.
        from .languages import LANGUAGES
        kwargs.setdefault('max_length', 3)
        kwargs.setdefault('choices', LANGUAGES)
        super().__init__(*args, **kwargs)


class PublishedManager(models.Manager):
    """Returns published entries, considering the publication
    dates and the published flag"""

    def get_queryset(self):
        return super().get_queryset().filter(Q(publish_from__isnull=True) | (Q(publish_from__lte=timezone.now())), Q(publish_to__isnull=True) | (Q(publish_to__gte=timezone.now())) , published=True )


class QgisFeedEntry(models.Model):
    """A feed entry for QGIS welcome page
    """

    title = models.CharField(_('Title'), max_length=255)
    image = ProcessedImageField([ResizeToFill(500, 354)], 'JPEG', {'quality': 60}, _('Image'),upload_to='feedimages/%Y/%m/%d/', height_field='image_height', width_field='image_width', max_length=None, blank=True, null=True, help_text=_('Landscape orientation, image will be cropped and scaled automatically to 500x354 px') )
    content = models.TextField()
    url = models.URLField(_('URL'), max_length=200, help_text=_('URL for more information link'))

    # Auto fields
    author = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, editable=False)
    image_height = models.IntegerField(_('Image height'), blank=True, null=True, editable=False)
    image_width = models.IntegerField(_('Image width'), blank=True, null=True, editable=False)
    created = models.DateField(_('Creation date'), auto_now=False, auto_now_add=True, editable=False)
    modified = models.DateField(_('Modification date'), auto_now=True, editable=False)

    # Options
    published = models.BooleanField(_('Published'), default=False, db_index=True)
    sticky = models.BooleanField(_('Sticky entry'), default=False, help_text=_('Check this option to keep this entry on top'))
    sorting = models.PositiveIntegerField(blank=False, null=False, default=0, verbose_name=_('Sorting order'), help_text=_('Increase to show at top of the list'), db_index=True)

    # Filters
    language_filter = QgisLanguageField(_('Language filter'), blank=True, null=True, help_text=_('The entry will be hidden to users who have not set a matching language filter'), db_index=True)
    spatial_filter = models.PolygonField(_('Spatial filter'), blank=True, null=True, help_text=_('The entry will be hidden to users who have set a location that does not match'))

    # Dates
    publish_from = models.DateTimeField(_('Publication start'), auto_now=False, auto_now_add=False, blank=True, null=True, db_index=True)
    publish_to = models.DateTimeField(_('Publication end'), auto_now=False, auto_now_add=False, blank=True, null=True, db_index=True)

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

    class Meta:
        db_table = ''
        managed = True
        verbose_name = _('QGIS Feed Entry')
        verbose_name_plural = _('QGIS Feed Entries')
        ordering = ('-sticky', '-sorting', '-publish_from')

    def set_request(self, request):
        self._request = request

    def save(self, *args, **kwargs):
        """Auto-set author and notify superadmin when entry is added"""
        if self.pk is None and hasattr(self, '_request') and self._request:
            self.author = self._request.user

        if self.published and self.publish_from is None:
            self.publish_from = timezone.now()

        super(QgisFeedEntry, self).save(*args, **kwargs)

        # TODO: Add send notification email to superadmin


class QgisUserVisit(models.Model):
    
    user_visit = models.OneToOneField(
        UserVisit,
        on_delete=models.CASCADE,
        primary_key=True
    )

    location = models.JSONField()

    # Mozilla/5.0 QGIS/32400/Fedora Linux (Workstation Edition)
    qgis_version = models.CharField(
        max_length=255,
        default='',
        blank=True
    )

    platform = models.CharField(
        max_length=255,
        default='',
        blank=True
    )


class DailyQgisUserVisit(models.Model):

    date = models.DateField(
        auto_now_add=True,
        blank=True
    )

    qgis_version = models.JSONField()

    platform = models.JSONField()

    country = models.JSONField()


def aggregate_user_visit_data():
    user_visits = QgisUserVisit.objects.all()

    # Group by date
    user_visit_dates = (
        user_visits.annotate(
            date=F('user_visit__timestamp__date')
        ).values_list('date', flat=True).distinct()
    )

    for user_visit_date in user_visit_dates:

        daily_visit, _ = DailyQgisUserVisit.objects.get_or_create(
            date=user_visit_date,
            defaults={
                'qgis_version': {},
                'platform': {},
                'country': {}
            }
        )

        qgis_user_visit = user_visits.filter(
            user_visit__timestamp__date=user_visit_date
        )

        total_platform_data = dict(
            qgis_user_visit.values(
                'platform'
            ).annotate(total_platform=Count('platform')).values_list(
                'platform', 'total_platform'
            )
        )

        total_country = dict(
            qgis_user_visit.filter(
                location__country_name__isnull=False
            ).values(
                'location__country_name'
            ).annotate(
                total_country=Count('location__country_name')
            ).values_list(
                'location__country_name', 'total_country'
            )
        )

        total_qgis_version = dict(
            qgis_user_visit.exclude(
                qgis_version=''
            ).values(
                'qgis_version'
            ).annotate(
                total_qgis_version=Count('qgis_version')
            ).values_list(
                'qgis_version', 'total_qgis_version'
            )
        )

        if total_platform_data:
            daily_platform_data = daily_visit.platform
            for platform, value in total_platform_data.items():
                if platform not in daily_platform_data:
                    daily_platform_data[platform] = (
                        value
                    )
                else:
                    daily_platform_data[platform] += (
                        value
                    )
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
                if qgis_version not in daily_qgis_version:
                    daily_qgis_version[qgis_version] = value
                else:
                    daily_qgis_version[qgis_version] += value
            daily_visit.qgis_version = daily_qgis_version

        daily_visit.save()

        UserVisit.objects.filter(
            timestamp__date=user_visit_date
        ).delete()
