# coding=utf-8
"""The real admin API client, with its HTTP session stubbed out.

Every other test uses ``FakeRealm``, so these are the ones that would notice a
method missing from the client itself.
"""

from unittest import mock

from django.test import SimpleTestCase

from ..keycloak import KeycloakAdminClient, KeycloakError

USERS_URL = "https://auth.example.org/admin/realms/qgis/users"


class CreateUserTest(SimpleTestCase):
    def setUp(self):
        self.keycloak = KeycloakAdminClient(
            server_url="https://auth.example.org",
            realm="qgis",
            client_id="feed-qgis-org-provisioner",
            client_secret="secret",
        )
        # A token that never expires, so no test reaches the token endpoint.
        self.keycloak._token = "token"
        self.keycloak._token_expires_at = float("inf")

    def create(self, location):
        reply = mock.Mock(status_code=201, headers={"Location": location})
        with mock.patch.object(
            self.keycloak._session, "request", return_value=reply
        ) as request:
            sub = self.keycloak.create_user({"username": "alice"})
        return sub, request

    def test_it_returns_the_subject_keycloak_generated(self):
        sub, request = self.create(f"{USERS_URL}/0b5e7c1a-42")

        self.assertEqual(sub, "0b5e7c1a-42")
        self.assertEqual(request.call_args.args, ("POST", USERS_URL))
        self.assertEqual(request.call_args.kwargs["json"], {"username": "alice"})

    def test_a_reply_without_a_location_is_an_error(self):
        with self.assertRaises(KeycloakError):
            self.create("")
