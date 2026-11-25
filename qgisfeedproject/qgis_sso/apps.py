# coding=utf-8
"""Application configuration for the Keycloak SSO app."""

from django.apps import AppConfig


class QgisSsoConfig(AppConfig):
    name = "qgis_sso"
    verbose_name = "QGIS single sign-on"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Connecting the audit receivers here rather than at import time keeps
        # them out of the way of management commands that never authenticate.
        from . import signals  # noqa: F401
