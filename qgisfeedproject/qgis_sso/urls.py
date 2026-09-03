# coding=utf-8
"""URLs for the SSO login journey."""

from django.urls import path

from . import views

app_name = "qgis_sso"

urlpatterns = [
    path("sign-in-failed/", views.sign_in_failed, name="sign_in_failed"),
]
