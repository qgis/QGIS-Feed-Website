# coding=utf-8
"""Everything single sign-on serves, under ``/sso/``.

The login journey, the account's own profile, the help page, and the
maintenance screens. They share one namespace because they are one app.
``help/`` is open to anybody, because the reader who needs it most is often the
one who cannot sign in. ``manage/`` is the only superuser-only part, and the
views enforce that themselves.
"""

from django.urls import path

from . import views, views_manage, views_profile

app_name = "qgis_sso"

urlpatterns = [
    path("help/", views.HelpView.as_view(), name="help"),
    path("sign-in-failed/", views.sign_in_failed, name="sign_in_failed"),
    path("profile/", views_profile.ProfileView.as_view(), name="profile"),
    path("manage/", views_manage.EnrolmentView.as_view(), name="enrolment"),
    path(
        "manage/link/",
        views_manage.IssuedLinkView.as_view(),
        name="issued_link",
    ),
    path(
        "manage/invite/new/",
        views_manage.InviteNewView.as_view(),
        name="invite_new",
    ),
    path(
        "manage/invite/existing/",
        views_manage.InviteExistingView.as_view(),
        name="invite_existing",
    ),
    path(
        "manage/revoke/<int:pk>/",
        views_manage.RevokeView.as_view(),
        name="revoke",
    ),
    path(
        "manage/reparent/<int:pk>/",
        views_manage.ReparentView.as_view(),
        name="reparent",
    ),
]
