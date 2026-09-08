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
from ..keycloak import KeycloakError

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


class FakeRealm:
    """A Keycloak realm that records what was asked of it.

    Stands in for :class:`~qgis_sso.keycloak.KeycloakAdminClient` so the tests
    assert on intent - who was created, who was emailed, whose link was handed
    over - without HTTP. Shared by the admin-action and enrolment-page suites,
    which exercise the same engine through different doors.
    """

    server_url = "https://auth.example.org"
    realm = "qgis"

    def __init__(self, existing_usernames=(), fail_on=()):
        self.existing = set(existing_usernames)
        self.fail_on = set(fail_on)
        self.created = []
        self.emailed = []
        self.linked = []
        self.link_payloads = []
        self.assigned = []
        #: Set to answer a magic-link request with a different subject, the
        #: case where a username has resolved to somebody else in the realm.
        self.answer_with_subject = None

    def client_uuid(self, client_id):
        return "client-uuid"

    def client_roles(self, client_uuid):
        return {
            "admin": {"id": "r-admin", "name": "admin"},
            "reviewer": {"id": "r-reviewer", "name": "reviewer"},
            "author": {"id": "r-author", "name": "author"},
        }

    def find_user_by_username(self, username):
        return {"id": "existing"} if username in self.existing else None

    def create_user(self, payload):
        if payload["username"] in self.fail_on:
            raise KeycloakError("create failed")
        self.created.append(payload)
        return f"sub-{payload['username']}"

    def assign_client_roles(self, user_id, client_uuid, roles):
        self.assigned.append((user_id, [role["name"] for role in roles]))

    def execute_actions_email(self, sub, actions, **kwargs):
        self.emailed.append(sub)

    def magic_link(self, username, **kwargs):
        """Stand in for PhaseTwo's magic-link endpoint, keyed on the username."""
        if username in self.fail_on:
            raise KeycloakError("no link for you")
        self.linked.append(username)
        self.link_payloads.append(dict(kwargs, username=username))
        sub = self.answer_with_subject or f"sub-{username}"
        return (
            sub,
            f"https://auth.example.org/realms/qgis/login-actions/token?key={sub}",
        )


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
