# coding=utf-8
"""URLs for the enrolment page.

Separate from :mod:`qgis_sso.urls`, which carries the public login journey and
is mounted under ``/sso/``. These are maintenance screens and live under
``/manage/`` with the rest of them.
"""

from django.urls import path

from . import views_manage

app_name = "qgis_sso_manage"

urlpatterns = [
    path("", views_manage.EnrolmentView.as_view(), name="enrolment"),
    path("create/", views_manage.CreateAccountsView.as_view(), name="create"),
]
