# coding=utf-8
"""Routes under ``/sso/``. The help page is open to anybody."""

from django.urls import path

from . import views, views_profile

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
]
