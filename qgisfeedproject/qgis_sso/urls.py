# coding=utf-8
"""Routes under ``/sso/``. The help page is open to anybody.

Each view under ``manage/`` says who may use it. A row action takes the row's
id and checks the same rule the list applied to that row.
"""

from django.urls import path

from . import views, views_manage, views_profile

app_name = "qgis_sso"

urlpatterns = [
    path("help/", views.HelpView.as_view(), name="help"),
    path("sign-in-failed/", views.sign_in_failed, name="sign_in_failed"),
    path("profile/", views_profile.ProfileView.as_view(), name="profile"),
    path(
        "profile/passkeys/",
        views_profile.PasskeyListView.as_view(),
        name="passkey_list",
    ),
    path("manage/", views_manage.EnrolmentView.as_view(), name="enrolment"),
    path(
        "manage/link/",
        views_manage.IssuedLinkView.as_view(),
        name="issued_link",
    ),
    path(
        "manage/send-email/<int:pk>/",
        views_manage.SendEmailView.as_view(),
        name="send_email",
    ),
    path(
        "manage/cancel-invitation/<int:pk>/",
        views_manage.CancelInvitationView.as_view(),
        name="cancel_invitation",
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
]
