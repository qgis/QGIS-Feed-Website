# coding=utf-8
"""A small Keycloak admin API client for provisioning.

Deliberately narrow: it does what :mod:`qgis_sso.provisioning` needs and
nothing more. The credentials it uses belong to a dedicated service-account
client holding only ``view-users``, ``manage-users`` and ``view-clients``; the
web application's own client must never hold them, because a compromise of the
site would otherwise be a compromise of the realm.
"""

import logging
import time
from urllib.parse import quote, urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class KeycloakError(RuntimeError):
    """A Keycloak admin API call failed."""


class KeycloakAdminClient:
    """Client-credentials access to one realm's admin API."""

    def __init__(
        self,
        server_url=None,
        realm=None,
        client_id=None,
        client_secret=None,
        timeout=DEFAULT_TIMEOUT,
    ):
        self.server_url = (
            server_url or getattr(settings, "SSO_KEYCLOAK_SERVER_URL", "")
        ).rstrip("/")
        self.realm = realm or getattr(settings, "SSO_KEYCLOAK_REALM", "qgis")
        self.client_id = client_id or getattr(settings, "SSO_PROVISIONER_CLIENT_ID", "")
        self.client_secret = client_secret or getattr(
            settings, "SSO_PROVISIONER_CLIENT_SECRET", ""
        )
        self.timeout = timeout
        self._session = requests.Session()
        self._token = None
        self._token_expires_at = 0.0

        missing = [
            name
            for name, value in (
                ("SSO_KEYCLOAK_SERVER_URL", self.server_url),
                ("SSO_PROVISIONER_CLIENT_ID", self.client_id),
                ("SSO_PROVISIONER_CLIENT_SECRET", self.client_secret),
            )
            if not value
        ]
        if missing:
            raise KeycloakError(
                "Keycloak provisioning is not configured; missing "
                + ", ".join(missing)
                + ". The client secret belongs in settings_local, never in the "
                "process environment."
            )

    # -- plumbing ----------------------------------------------------------

    @property
    def admin_base(self):
        return f"{self.server_url}/admin/realms/{self.realm}/"

    def access_token(self):
        """Fetch, and cache until shortly before expiry, a service-account token."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        response = self._session.post(
            f"{self.server_url}/realms/{self.realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        if response.status_code != 200:
            # The response body can echo the request; it is not logged.
            raise KeycloakError(
                f"Could not obtain a service-account token: HTTP {response.status_code}"
            )
        payload = response.json()
        self._token = payload["access_token"]
        # Renew a minute early so a long batch does not fail mid-run.
        self._token_expires_at = time.monotonic() + max(
            30, int(payload.get("expires_in", 60)) - 60
        )
        return self._token

    def request(self, method, path, **kwargs):
        url = urljoin(self.admin_base, path.lstrip("/"))
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token()}"
        response = self._session.request(
            method, url, headers=headers, timeout=self.timeout, **kwargs
        )
        if response.status_code >= 400:
            raise KeycloakError(
                f"{method} {path} failed: HTTP {response.status_code} "
                f"{response.text[:500]}"
            )
        return response

    # -- users --------------------------------------------------------------

    def find_user_by_username(self, username):
        """Return the realm user with exactly this username, or None.

        ``exact=true`` matters: without it Keycloak does a prefix search and
        ``alice`` would match ``alice2``.
        """
        response = self.request(
            "GET", "users", params={"username": username, "exact": "true", "max": 2}
        )
        users = response.json()
        if not users:
            return None
        if len(users) > 1:  # pragma: no cover - Keycloak enforces uniqueness
            raise KeycloakError(f"More than one realm user named {username!r}")
        return users[0]

    def create_user(self, payload):
        """Create a realm user and return the generated subject.

        Keycloak returns the new id only in the ``Location`` header, and we
        read it back rather than proposing an id of our own so that Keycloak
        stays the sole authority for subject generation.
        """
        response = self.request("POST", "users", json=payload)
        location = response.headers.get("Location", "")
        sub = location.rstrip("/").rsplit("/", 1)[-1]
        if not sub:
            raise KeycloakError(
                "Keycloak accepted the user but returned no Location header"
            )
        return sub

    def execute_actions_email(
        self, user_id, actions, client_id, redirect_uri, lifespan
    ):
        """Send the account-setup email carrying the required actions."""
        self.request(
            "PUT",
            f"users/{quote(user_id)}/execute-actions-email",
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "lifespan": int(lifespan),
            },
            json=list(actions),
        )

    # -- clients and roles ---------------------------------------------------

    def client_uuid(self, client_id):
        """Resolve a client id to the internal UUID the role endpoints want."""
        response = self.request("GET", "clients", params={"clientId": client_id})
        clients = response.json()
        if not clients:
            raise KeycloakError(f"No client {client_id!r} in realm {self.realm!r}")
        return clients[0]["id"]

    def client_roles(self, client_uuid):
        """Map role name to role representation for one client."""
        response = self.request("GET", f"clients/{quote(client_uuid)}/roles")
        return {role["name"]: role for role in response.json()}

    def assign_client_roles(self, user_id, client_uuid, roles):
        """Grant client roles to a user. Idempotent on Keycloak's side."""
        if not roles:
            return
        self.request(
            "POST",
            f"users/{quote(user_id)}/role-mappings/clients/{quote(client_uuid)}",
            json=list(roles),
        )
