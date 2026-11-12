# coding=utf-8
"""Application configuration for the Keycloak SSO app."""

from django.apps import AppConfig


class QgisSsoConfig(AppConfig):
    name = "qgis_sso"
    verbose_name = "QGIS single sign-on"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from . import signals  # noqa: F401
