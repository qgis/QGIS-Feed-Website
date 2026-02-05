from django.urls import path

from . import views

urlpatterns = [
    path("", views.QgisEntriesView.as_view(), name="all"),
    path("<int:pk>/", views.FeedEntryDetailView.as_view(), name="feed_detail"),
    path("manage/", views.FeedsListView.as_view(), name="feeds_list"),
    path("manage/add/", views.FeedEntryAddView.as_view(), name="feed_entry_add"),
    path(
        "manage/update/<int:pk>/",
        views.FeedEntryUpdateView.as_view(),
        name="feed_entry_update",
    ),
    path(
        "manage/review/<int:pk>/",
        views.FeedEntryReviewActionView.as_view(),
        name="feed_entry_review_action",
    ),
    # Social syndication
    path(
        "manage/share-mastodon/<int:pk>/",
        views.FeedEntryShareMastodonView.as_view(),
        name="feed_entry_share_mastodon",
    ),
    path(
        "manage/share-bluesky/<int:pk>/",
        views.FeedEntryShareBlueskyView.as_view(),
        name="feed_entry_share_bluesky",
    ),
    path(
        "manage/share-telegram/<int:pk>/",
        views.FeedEntryShareTelegramView.as_view(),
        name="feed_entry_share_telegram",
    ),
]
