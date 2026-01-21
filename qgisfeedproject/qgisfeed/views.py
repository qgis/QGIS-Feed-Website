# coding=utf-8
""" "Views for QGIS Welcome Page News Feed, returns JSON data

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""

__author__ = "elpaso@itopen.it"
__date__ = "2019-05-07"
__copyright__ = "Copyright 2019, ItOpen"


import html
import json
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.views import View

from .forms import (
    FeedEntryFilterForm,
    FeedItemForm,
    FeedSocialSyndicationForm,
    HomePageFilterForm,
)
from .languages import LANGUAGE_KEYS
from .models import CharacterLimitConfiguration, QgisFeedEntry
from .social_utils import BlueskyManager, MastodonManager, TelegramManager
from .utils import (
    can_edit_entry,
    can_publish_entry,
    can_review_entry,
    can_submit_for_review,
    create_revision_snapshot,
    get_field_max_length,
    get_location,
    notify_author_approved,
    notify_author_changes_requested,
    notify_author_published,
    notify_reviewers,
    notify_reviewers_resubmitted,
    parse_remote_addr,
)

QGISFEED_MAX_RECORDS = getattr(settings, "QGISFEED_MAX_RECORDS", 20)

# Decorator
staff_required = user_passes_test(lambda u: u.is_staff)


class BadRequestException(Exception):
    pass


class QgisEntriesView(View):
    """Views for QGIS Welcome Page News Feed, returns JSON data
    for QGIS and a web page view for the browser

    accepted parameters:
    - lang=[A comma separated list of 2 letter iso codes for the language]
    - json=[Set to 1 to receive JSON response from a non-QGIS agent]
    """

    form_class = HomePageFilterForm
    template_name = "feeds/feed_home_page.html"

    def get_filters(self, request):
        """Extract filters from the request and checks validity

        :param request: the HTTP request
        :type request: Django HTTPRequest
        :return: a dictionary with lang and location (WKT Point) parameters
        :rtype: dict
        """
        filters = {}
        if request.GET.get("lang"):
            langs = request.GET.get("lang")
            # Accept comma-separated language codes
            lang_list = [l.strip() for l in langs.split(",")]
            valid_langs = []
            pattern = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")
            for lang in lang_list:
                if pattern.match(lang):
                    lang_code = lang[:2]
                    if lang_code in LANGUAGE_KEYS:
                        valid_langs.append(lang_code)
                    else:
                        raise BadRequestException("Invalid language parameter.")
                else:
                    raise BadRequestException("Invalid language parameter.")
            if valid_langs:
                filters["lang"] = valid_langs

        # Build location filter from the request's remote address
        # as QGIS doesn't send the location in the request
        # This is used to filter entries by location
        remote_addr = parse_remote_addr(request)
        if remote_addr:
            location = get_location(remote_addr)
            if location:
                GEOSGeometry(location)
                filters["location"] = location

        if request.GET.get("publish_from"):
            try:
                filters["after"] = timezone.make_aware(
                    timezone.datetime.strptime(
                        request.GET.get("publish_from"), "%Y-%m-%d"
                    )
                )
            except ValueError:
                raise BadRequestException("Invalid after parameter.")

        if request.GET.get("after") is not None:
            try:
                filters["after"] = timezone.make_aware(
                    timezone.datetime.fromtimestamp(float(request.GET.get("after")))
                )
            except ValueError:
                raise BadRequestException("Invalid after parameter.")

        return filters

    def get(self, request):
        form = self.form_class(request.GET)
        data = []

        try:
            filters = self.get_filters(request)
        except BadRequestException as ex:
            return HttpResponseBadRequest("%s" % ex)

        user_agent = request.META.get("HTTP_USER_AGENT")
        qgis_version = None
        if "QGIS/" in str(user_agent):
            try:
                qgis_pos = user_agent.find("QGIS/")
                if qgis_pos >= 0:
                    qgis_version = int(int(user_agent[qgis_pos + 5 : qgis_pos + 10]))
            except ValueError:
                pass

        # "after" is only set by QGIS client on requests after the first one.
        # The logic here is for QGIS >= 3.36 to send anything "published=True"
        # that has been modified since the last feed check, and exclude upcoming entries,
        # we also need to send expired items because setting the "publish_to" publication end
        # time in the past is the way to remove them from the client cache and
        # because entries might have been updated.
        # For older QGIS < 3.36 we only send published items, optionally filtered
        # by >= `after`.

        after = filters.get("after")
        if after is None:
            qs = QgisFeedEntry.published_entries.all()
        else:
            # fallback to original behavior if
            # QGIS Version is not specified or QGIS < 3.36
            if qgis_version is None or qgis_version < 33600:
                qs = QgisFeedEntry.published_entries.all()
                qs = qs.filter(publish_from__gte=filters.get("after"))
            # For update capabilities require QGIS >= 3.36,
            # exclude upcoming entries
            else:
                qs = QgisFeedEntry.objects.all()
                now = timezone.now()
                qs = qs.filter(modified__gte=after, published=True).exclude(
                    publish_from__gte=now
                )

        # Get filters for lang and lat/lon
        lang = filters.get("lang")
        location = filters.get("location")
        if lang is not None and location is not None:
            # Filter for records that either:
            # 1. Match the specified language, OR
            # 2. Contain the specified location, OR
            # 3. Have no language or location filters set (null values)
            # This ensures we get all potentially relevant records while respecting filter settings
            qs = qs.filter(
                Q(language_filter__in=lang)
                | Q(spatial_filter__contains=location)
                | Q(language_filter__isnull=True, spatial_filter__isnull=True)
            )
        elif lang is not None:
            # Filter for records that either:
            # 1. Match the specified language, OR
            # 2. Have no filters set at all (both language and location null)
            # This provides language-specific results while including unfiltered records
            qs = qs.filter(
                Q(language_filter__in=lang)
                | Q(language_filter__isnull=True, spatial_filter__isnull=True)
            )
        elif location is not None:
            # Filter for records that either:
            # 1. Contain the specified location, OR
            # 2. Have no filters set at all (both language and location null)
            # This provides location-specific results while including unfiltered records
            qs = qs.filter(
                Q(spatial_filter__contains=location)
                | Q(language_filter__isnull=True, spatial_filter__isnull=True)
            )

        for record in qs.values(
            "pk",
            "publish_from",
            "publish_to",
            "title",
            "image",
            "content",
            "url",
            "sticky",
        )[:QGISFEED_MAX_RECORDS]:
            if record["publish_from"]:
                record["publish_from"] = record["publish_from"].timestamp()
            if record["publish_to"]:
                record["publish_to"] = record["publish_to"].timestamp()
            if record["image"]:
                record["image"] = request.build_absolute_uri(
                    settings.MEDIA_URL + record["image"]
                )
            data.append(record)

        data_json = json.dumps(data, indent=(2 if settings.DEBUG else 0))
        if "qgis" in str(user_agent).lower() or request.GET.get("json", "") == "1":
            return HttpResponse(data_json, content_type="application/json")
        else:
            args = {"data": data, "form": form, "data_json": json.dumps(data, indent=2)}
            return render(request, self.template_name, args)


@method_decorator(staff_required, name="dispatch")
@method_decorator(permission_required("qgisfeed.view_qgisfeedentry"), name="dispatch")
class FeedsListView(View):
    """
    List of feeds
    This view renders a paginated, sorted according
    to the request parameters list of feeds
    """

    form_class = FeedEntryFilterForm
    template_name = "feeds/feeds_list.html"
    items_per_page = 15  # Set the number of items per page

    def get(self, request):
        form = self.form_class(request.GET)
        feeds_entry = QgisFeedEntry.objects.all()

        # Get filter parameters from the query string
        if form.is_valid():
            title = form.cleaned_data.get("title")
            if title:
                feeds_entry = feeds_entry.filter(title__icontains=title)

            author = form.cleaned_data.get("author")
            if author:
                feeds_entry = feeds_entry.filter(author__username__icontains=author)

            language_filter = form.cleaned_data.get("language_filter")
            if language_filter:
                feeds_entry = feeds_entry.filter(language_filter=language_filter)

            publish_from = form.cleaned_data.get("publish_from")
            if publish_from:
                feeds_entry = feeds_entry.filter(publish_from__gt=publish_from)

            publish_to = form.cleaned_data.get("publish_to")
            if publish_to:
                feeds_entry = feeds_entry.filter(publish_to__lt=publish_to)

            need_review = form.cleaned_data.get("need_review")
            if need_review:
                published = not bool(int(need_review))
                feeds_entry = feeds_entry.filter(published=published)

        # Get sorting parameters from the query string
        sort_by = request.GET.get("sort_by", "publish_from")
        current_order = request.GET.get("order", "desc")

        if current_order == "asc":
            feeds_entry = feeds_entry.order_by(sort_by)
            next_order = "desc"
        else:
            next_order = "asc"
            feeds_entry = feeds_entry.order_by(f"-{sort_by}")

        # Get the count of all/filtered entries
        count = feeds_entry.count()

        # Paginate the list of feeds
        page = request.GET.get("page", 1)
        paginator = Paginator(feeds_entry, self.items_per_page)

        try:
            feeds_entry = paginator.page(page)
        except PageNotAnInteger:
            feeds_entry = paginator.page(1)
        except EmptyPage:
            feeds_entry = paginator.page(paginator.num_pages)

        user = request.user
        user_can_add = user.has_perm("qgisfeed.add_qgisfeedentry")
        user_can_change = user.has_perm("qgisfeed.change_qgisfeedentry")

        return render(
            request,
            self.template_name,
            {
                "feeds_entry": feeds_entry,
                "sort_by": sort_by,
                "order": next_order,
                "current_order": current_order,
                "form": form,
                "count": count,
                "user_can_change": user_can_change,
                "user_can_add": user_can_add,
            },
        )


@method_decorator(staff_required, name="dispatch")
@method_decorator(permission_required("qgisfeed.add_qgisfeedentry"), name="dispatch")
class FeedEntryAddView(View):
    """
    View to add a feed entry item
    """

    form_class = FeedItemForm
    template_name = "feeds/feed_item_form.html"

    def get(self, request):
        msg = None
        success = False
        user = request.user
        user_is_approver = user.has_perm("qgisfeed.publish_qgisfeedentry")
        form = self.form_class(user=user)

        args = {
            "form": form,
            "msg": msg,
            "success": success,
            "published": False,
            "user_is_approver": user_is_approver,
            "content_max_length": get_field_max_length(
                CharacterLimitConfiguration, field_name="content"
            ),
        }

        return render(request, self.template_name, args)

    def post(self, request):
        msg = None
        success = False
        user = request.user
        user_is_approver = user.has_perm("qgisfeed.publish_qgisfeedentry")
        form = self.form_class(request.POST, request.FILES, user=user)

        if form.is_valid():
            with transaction.atomic():
                new_object = form.save(commit=False)
                new_object.set_request(
                    request
                )  # Pass the 'request' to get the user in the model

                # Set initial status
                new_object.status = QgisFeedEntry.DRAFT
                new_object.save()

                # Handle reviewers assignment
                if form.cleaned_data.get("reviewers"):
                    reviewer_ids = form.cleaned_data.get("reviewers")
                    reviewers_to_add = User.objects.filter(pk__in=reviewer_ids)
                    new_object.reviewers.set(reviewers_to_add)

                success = True

                # Check if submitting for review
                submit_for_review = request.POST.get("submit_for_review")
                if submit_for_review:
                    new_object.status = QgisFeedEntry.PENDING_REVIEW
                    new_object.save()

                    # Notify assigned reviewers or all reviewers
                    if new_object.reviewers.exists():
                        recipients = [
                            r.email
                            for r in new_object.reviewers.all()
                            if r.email and r.has_perm("qgisfeed.publish_qgisfeedentry")
                        ]
                    else:
                        # No specific reviewers, notify all possible reviewers
                        reviewers = User.objects.filter(
                            is_active=True, email__isnull=False
                        ).exclude(email="")
                        recipients = [
                            u.email
                            for u in reviewers
                            if u.has_perm("qgisfeed.publish_qgisfeedentry")
                        ]

                    if recipients and len(recipients) > 0:
                        notify_reviewers(request.user, request, recipients, new_object)

                    messages.success(request, "Feed entry submitted for review!")
                else:
                    messages.success(request, "Draft saved successfully!")

                return redirect("feeds_list")
        else:
            success = False
            msg = "Form is not valid"

        args = {
            "form": form,
            "msg": msg,
            "success": success,
            "published": False,
            "user_is_approver": user_is_approver,
            "content_max_length": get_field_max_length(
                CharacterLimitConfiguration, field_name="content"
            ),
        }

        return render(request, self.template_name, args)


@method_decorator(staff_required, name="dispatch")
class FeedEntryUpdateView(View):
    """
    View to update/publish a feed entry item
    """

    form_class = FeedItemForm
    template_name = "feeds/feed_item_form.html"

    def get(self, request, pk):
        msg = None
        success = False
        feed_entry = get_object_or_404(QgisFeedEntry, pk=pk)
        user = request.user

        # Check if user can edit this entry
        if not can_edit_entry(user, feed_entry):
            messages.error(request, "You don't have permission to edit this entry.")
            return redirect("feeds_list")

        user_is_approver = user.has_perm("qgisfeed.publish_qgisfeedentry")
        user_is_author = feed_entry.author == user
        user_can_review = can_review_entry(user, feed_entry)
        user_can_submit = can_submit_for_review(user, feed_entry)
        user_can_publish = can_publish_entry(user, feed_entry)

        form = self.form_class(instance=feed_entry, user=user)

        # Get review history
        reviews = feed_entry.reviews.all()

        # Get reviewer statuses
        reviewer_statuses = feed_entry.get_all_reviewer_statuses()

        # Get revision history
        revisions = feed_entry.revisions.all()[:5]  # Show last 5 revisions

        # Initialize the social syndication form with values from feed_entry
        post_link = f"\n\nLink: {feed_entry.url}" if feed_entry.url else ""
        initial_data = {
            "post_content": f"{feed_entry.title}\n\n{html.unescape(strip_tags(feed_entry.content))}{post_link}",
        }
        social_syndication_form = FeedSocialSyndicationForm(initial=initial_data)

        args = {
            "form": form,
            "msg": msg,
            "success": success,
            "published": feed_entry.published,
            "feed_entry": feed_entry,
            "user_is_approver": user_is_approver,
            "user_is_author": user_is_author,
            "user_can_review": user_can_review,
            "user_can_submit": user_can_submit,
            "user_can_publish": user_can_publish,
            "reviews": reviews,
            "reviewer_statuses": reviewer_statuses,
            "revisions": revisions,
            "content_max_length": get_field_max_length(
                CharacterLimitConfiguration, field_name="content"
            ),
            "social_syndication_form": social_syndication_form,
        }

        return render(request, self.template_name, args)

    def post(self, request, pk):
        msg = None
        success = False
        feed_entry = get_object_or_404(QgisFeedEntry, pk=pk)
        user = request.user

        # Check if user can edit this entry
        if not can_edit_entry(user, feed_entry):
            messages.error(request, "You don't have permission to edit this entry.")
            return redirect("feeds_list")

        user_is_approver = user.has_perm("qgisfeed.publish_qgisfeedentry")
        user_is_author = feed_entry.author == user

        # Store original status before any changes
        original_status = feed_entry.status
        feed_entry.published

        form = self.form_class(
            request.POST, request.FILES, instance=feed_entry, user=user
        )

        if form.is_valid():
            with transaction.atomic():
                instance = form.save(commit=False)

                # Create revision snapshot if content changed
                if instance.pk and (
                    instance.title != feed_entry.title
                    or instance.content != feed_entry.content
                    or instance.url != feed_entry.url
                ):
                    change_summary = request.POST.get("change_summary", "Entry updated")
                    create_revision_snapshot(feed_entry, user, change_summary)

                # Save the instance first
                instance.save()

                # Handle reviewers assignment (update ManyToMany relationship)
                if form.cleaned_data.get("reviewers"):
                    reviewer_ids = form.cleaned_data.get("reviewers")
                    reviewers_to_add = User.objects.filter(pk__in=reviewer_ids)
                    instance.reviewers.set(reviewers_to_add)

                # Handle different actions
                action = request.POST.get("action", "save")

                if action == "submit_for_review":
                    # Author submitting for review
                    if can_submit_for_review(user, instance):
                        instance.status = QgisFeedEntry.PENDING_REVIEW
                        instance.save()

                        # Notify reviewers (resubmission case)
                        if original_status == QgisFeedEntry.CHANGES_REQUESTED:
                            notify_reviewers_resubmitted(instance, request)
                        else:
                            # Submission - send to assigned reviewers
                            if instance.reviewers.exists():
                                recipients = [
                                    r.email
                                    for r in instance.reviewers.all()
                                    if r.email
                                    and r.has_perm("qgisfeed.publish_qgisfeedentry")
                                ]
                            else:
                                # No specific reviewers, notify all
                                reviewers = User.objects.filter(
                                    is_active=True, email__isnull=False
                                ).exclude(email="")
                                recipients = [
                                    u.email
                                    for u in reviewers
                                    if u.has_perm("qgisfeed.publish_qgisfeedentry")
                                ]

                            if recipients:
                                notify_reviewers(user, request, recipients, instance)

                        messages.success(request, "Entry submitted for review!")
                    else:
                        messages.error(request, "Cannot submit this entry for review.")

                elif action == "publish":
                    # Author or reviewer publishing approved entry
                    if can_publish_entry(user, instance):
                        instance.status = QgisFeedEntry.PUBLISHED
                        instance.published = True
                        if instance.publish_from is None:
                            instance.publish_from = timezone.now()
                        instance.save()

                        # Notify author if published by reviewer
                        if not user_is_author:
                            notify_author_published(instance, request)
                        messages.success(request, "Entry published successfully!")
                    else:
                        messages.error(
                            request, "Entry must be approved before publishing."
                        )

                elif action == "save":
                    # Regular save
                    # If author is editing published entry, unpublish and send back to review
                    if original_status == QgisFeedEntry.PUBLISHED and user_is_author:
                        instance.status = QgisFeedEntry.PENDING_REVIEW
                        instance.published = False
                        instance.save()

                        # Notify reviewers
                        notify_reviewers_resubmitted(instance, request)
                        messages.info(
                            request,
                            "Published entry updated. It has been sent back for review before republishing.",
                        )
                    # If author is editing approved entry, send back to review
                    elif original_status == QgisFeedEntry.APPROVED and user_is_author:
                        instance.status = QgisFeedEntry.PENDING_REVIEW
                        instance.save()

                        # Notify reviewers about resubmission
                        notify_reviewers_resubmitted(instance, request)
                        messages.info(
                            request,
                            "Approved entry updated. It has been sent back for review.",
                        )
                    # If author is editing entry under review, keep it under review
                    elif (
                        original_status == QgisFeedEntry.PENDING_REVIEW
                        and user_is_author
                    ):
                        # Keep status as pending review
                        instance.save()
                        messages.success(
                            request, "Changes saved! Entry remains under review."
                        )
                    else:
                        instance.save()
                        messages.success(request, "Changes saved successfully!")

                success = True
                return redirect("feed_entry_update", pk=pk)
        else:
            success = False
            msg = "Form is not valid"

        # Re-render with errors
        reviews = feed_entry.reviews.all()
        reviewer_statuses = feed_entry.get_all_reviewer_statuses()
        revisions = feed_entry.revisions.all()[:5]

        args = {
            "form": form,
            "msg": msg,
            "success": success,
            "published": feed_entry.published,
            "feed_entry": feed_entry,
            "user_is_approver": user_is_approver,
            "user_is_author": user_is_author,
            "user_can_review": can_review_entry(user, feed_entry),
            "user_can_submit": can_submit_for_review(user, feed_entry),
            "user_can_publish": can_publish_entry(user, feed_entry),
            "reviews": reviews,
            "reviewer_statuses": reviewer_statuses,
            "revisions": revisions,
            "content_max_length": get_field_max_length(
                CharacterLimitConfiguration, field_name="content"
            ),
        }

        return render(request, self.template_name, args)


class FeedEntryDetailView(View):
    """
    View to display a feed entry item
    """

    template_name = "feeds/feed_item_detail.html"

    def get(self, request, pk):
        feed_entry = get_object_or_404(QgisFeedEntry, pk=pk)
        return render(request, self.template_name, {"feed_entry": feed_entry})


@method_decorator(staff_required, name="dispatch")
class FeedEntryReviewActionView(View):
    """
    Handle reviewer actions and author comments
    Authors can comment, reviewers can approve/request changes/comment
    """

    def post(self, request, pk):
        from qgisfeed.models import FeedEntryReview

        entry = get_object_or_404(QgisFeedEntry, pk=pk)

        # Authors can comment on their own entries
        # Authors with publish permission or superusers can also approve/request changes
        is_author = entry.author == request.user
        can_review = can_review_entry(request.user, entry)

        if not is_author and not can_review:
            messages.error(
                request, "You don't have permission to comment on this entry."
            )
            return redirect("feed_entry_update", pk=pk)

        action = request.POST.get("action")
        comment = request.POST.get("comment", "")

        # Authors without publish permission can only add comments
        # Authors with publish permission or superusers can approve/request changes
        if is_author and action != FeedEntryReview.ACTION_COMMENT:
            if not can_review:
                messages.error(
                    request,
                    "Authors can only add comments, not approve or request changes.",
                )
                return redirect("feed_entry_update", pk=pk)

        if not comment:
            messages.error(request, "Please provide a comment.")
            return redirect("feed_entry_update", pk=pk)

        with transaction.atomic():
            # Create review record
            review = FeedEntryReview.objects.create(
                entry=entry, reviewer=request.user, action=action, comment=comment
            )

            # Update entry status based on action
            if action == FeedEntryReview.ACTION_APPROVE:
                # Set to APPROVED after any reviewer approves
                # (not waiting for all reviewers)
                entry.status = QgisFeedEntry.APPROVED
                entry.save()
                messages.success(request, "Entry approved! You can now publish it.")
                # Notify author
                notify_author_approved(entry, review, request)

            elif action == FeedEntryReview.ACTION_REQUEST_CHANGES:
                entry.status = QgisFeedEntry.CHANGES_REQUESTED
                entry.save()
                messages.info(request, "Changes requested. Author will be notified.")

                # Notify author
                notify_author_changes_requested(entry, review, request)

            elif action == FeedEntryReview.ACTION_COMMENT:
                if is_author:
                    messages.success(
                        request, "Your comment has been added for reviewers to see."
                    )
                else:
                    messages.success(request, "Comment added to review.")

        return redirect("feed_entry_update", pk=pk)


@method_decorator(staff_required, name="dispatch")
@method_decorator(
    permission_required("qgisfeed.publish_qgisfeedentry"), name="dispatch"
)
class FeedEntryShareMastodonView(View):
    """
    View to share a feed entry item to Mastodon
    """

    def post(self, request, pk):
        feed_entry = get_object_or_404(QgisFeedEntry, pk=pk)
        mastodon_manager = MastodonManager()
        try:
            content_text = html.unescape(strip_tags(feed_entry.content))
            post_link = f"\n\nLink: {feed_entry.url}" if feed_entry.url else ""
            post_content = request.POST.get(
                "post_content", f"{feed_entry.title}\n\n{content_text}{post_link}"
            )
            status = mastodon_manager.create_post(
                status=post_content,
                image_path=feed_entry.image.path if feed_entry.image else None,
            )
            if status:
                messages.success(
                    request,
                    f"Successfully shared to Mastodon! View post at: <a href='{status.url}' target='_blank'>{status.url}</a>",
                    fail_silently=True,
                )
            else:
                messages.error(
                    request, "Failed to share to Mastodon.", fail_silently=True
                )
        except Exception as e:
            messages.error(
                request, f"Error sharing to Mastodon: {str(e)}", fail_silently=True
            )
        return redirect("feeds_list")


@method_decorator(staff_required, name="dispatch")
@method_decorator(
    permission_required("qgisfeed.publish_qgisfeedentry"), name="dispatch"
)
class FeedEntryShareBlueskyView(View):
    """
    View to share a feed entry item to Bluesky
    """

    def post(self, request, pk):
        feed_entry = get_object_or_404(QgisFeedEntry, pk=pk)
        bluesky_manager = BlueskyManager()
        try:
            content_text = html.unescape(strip_tags(feed_entry.content))
            post_link = f"\n\nLink: {feed_entry.url}" if feed_entry.url else ""
            post_content = request.POST.get(
                "post_content", f"{feed_entry.title}\n\n{content_text}{post_link}"
            )
            text_builder = bluesky_manager.build_text(content=post_content)
            status = bluesky_manager.create_post(
                text_builder=text_builder,
                image=feed_entry.image.read() if feed_entry.image else None,
            )
            if status:
                handle = settings.BLUESKY_HANDLE
                post_id = status.uri.split("/")[-1]  # Extracts "xyz456" from the URI
                bluesky_post_url = f"https://bsky.app/profile/{handle}/post/{post_id}"
                messages.success(
                    request,
                    f"Successfully shared to Bluesky! View post at: <a href='{bluesky_post_url}' target='_blank'>{bluesky_post_url}</a>",
                    fail_silently=True,
                )
            else:
                messages.error(
                    request, "Failed to share to Bluesky.", fail_silently=True
                )
        except Exception as e:
            messages.error(
                request, f"Error sharing to Bluesky: {str(e)}", fail_silently=True
            )
        return redirect("feeds_list")


@method_decorator(staff_required, name="dispatch")
@method_decorator(permission_required("qgisfeed.delete_qgisfeedentry"), name="dispatch")
class FeedEntryShareTelegramView(View):
    """
    View to share a feed entry item to Telegram
    """

    def post(self, request, pk):
        feed_entry = get_object_or_404(QgisFeedEntry, pk=pk)
        telegram_manager = TelegramManager()
        try:
            content_text = strip_tags(feed_entry.content)
            post_link = f"\n\nLink: {feed_entry.url}" if feed_entry.url else ""
            post_content = request.POST.get(
                "post_content", f"{feed_entry.title}\n\n{content_text}{post_link}"
            )
            response = telegram_manager.send_message(
                message=post_content,
                image_path=feed_entry.image.path if feed_entry.image else None,
            )
            if response and response.get("ok"):
                messages.success(
                    request, f"Successfully shared to Telegram!", fail_silently=True
                )
            else:
                messages.error(
                    request, "Failed to share to Telegram.", fail_silently=True
                )
        except Exception as e:
            messages.error(
                request, f"Error sharing to Telegram: {str(e)}", fail_silently=True
            )
        return redirect("feeds_list")
