# coding=utf-8
"""Everything single sign-on serves, under ``/sso/``.

The login journey, the account's own profile, and the maintenance screens.
They share one namespace because they are one app; ``manage/`` is the only
part that is superuser-only, and the views enforce that themselves.
"""

from django.urls import path

from . import views, views_manage, views_profile

app_name = "qgis_sso"

urlpatterns = [
    path("sign-in-failed/", views.sign_in_failed, name="sign_in_failed"),
    path("profile/", views_profile.ProfileView.as_view(), name="profile"),
    path("manage/", views_manage.EnrolmentView.as_view(), name="enrolment"),
    path("manage/create/", views_manage.CreateAccountsView.as_view(), name="create"),
]
