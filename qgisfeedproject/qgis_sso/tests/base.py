# coding=utf-8
"""Shared fixtures for the SSO tests.

The backend is exercised through ``get_or_create_user``, which is the single
entry point the callback view uses, rather than through mocked HTTP. That
keeps the tests pinned to the behaviour that matters - which token gets an
account and which does not - instead of to the library's internals.
"""

import time

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase

from ..auth import QGISOIDCAuthenticationBackend

ISSUER = "https://auth.example.org/realms/qgis"
CLIENT_ID = "feed-qgis-org"

SSO_SETTINGS = {
    "SSO_ISSUER": ISSUER,
    "OIDC_RP_CLIENT_ID": CLIENT_ID,
    "SSO_MANAGED_GROUPS": ["qgisfeedentry_authors", "qgisfeedentry_approver"],
    "SSO_ROLE_MAP": {
        "admin": {
            "groups": ["qgisfeedentry_authors", "qgisfeedentry_approver"],
            "is_staff": True,
            "is_superuser": True,
        },
        "reviewer": {
            "groups": ["qgisfeedentry_authors", "qgisfeedentry_approver"],
            "is_staff": True,
            "is_superuser": False,
        },
        "author": {
            "groups": ["qgisfeedentry_authors"],
            "is_staff": True,
            "is_superuser": False,
        },
    },
}


def id_token_payload(sub="sub-1", **overrides):
    """A payload shaped like a verified Keycloak ID token."""
    payload = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": sub,
        "exp": time.time() + 300,
    }
    payload.update(overrides)
    return payload


def userinfo(sub="sub-1", **overrides):
    """A response shaped like Keycloak's userinfo endpoint with feed-roles."""
    claims = {
        "sub": sub,
        "preferred_username": "alice",
        "email": "alice@example.org",
        "email_verified": True,
        "feed_roles": [],
    }
    claims.update(overrides)
    return claims


class SsoTestCase(TestCase):
    """Base case that stubs the userinfo fetch and creates the site groups."""

    def setUp(self):
        super().setUp()
        self.backend = QGISOIDCAuthenticationBackend()
        self.backend.request = None
        self._ensure_groups()

    @staticmethod
    def _ensure_groups():
        """Create the groups qgisfeed's own receivers would create.

        Role mirroring only ever adds a user to a group that already exists,
        so without these the mirroring tests would pass vacuously.
        """
        for name, codenames in (
            ("qgisfeedentry_authors", ("view_qgisfeedentry", "add_qgisfeedentry")),
            (
                "qgisfeedentry_approver",
                ("view_qgisfeedentry", "add_qgisfeedentry", "change_qgisfeedentry"),
            ),
        ):
            group, _ = Group.objects.get_or_create(name=name)
            for codename in codenames:
                permission = Permission.objects.filter(codename=codename).first()
                if permission is not None:
                    group.permissions.add(permission)

    def authenticate(self, claims, payload=None):
        """Drive the backend as the callback view would.

        Returns the user, or None when the backend refused.
        """
        sub = claims["sub"]
        self.backend.get_userinfo = lambda *args, **kwargs: claims
        return self.backend.get_or_create_user(
            "access-token", "id-token", payload or id_token_payload(sub)
        )

    @staticmethod
    def make_user(username="alice", email="alice@example.org", **kwargs):
        kwargs.setdefault("is_active", True)
        return User.objects.create_user(
            username=username, email=email, password="not-the-point", **kwargs
        )

    @staticmethod
    def group_names(user):
        return set(user.groups.values_list("name", flat=True))
