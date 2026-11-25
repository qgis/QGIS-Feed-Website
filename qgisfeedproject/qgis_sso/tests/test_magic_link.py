# coding=utf-8
"""The call to PhaseTwo's magic-link endpoint.

Keycloak has no API that hands back a setup link, so this is the one place the
site talks to something that is not Keycloak itself. What it sends matters more
than usual: two of the fields below default the wrong way for us, and a link
returned for the wrong subject would sign the wrong person into an account.
"""

from unittest import mock

from django.test import TestCase, override_settings

from ..keycloak import KeycloakAdminClient, KeycloakError

CLIENT_SETTINGS = {
    "SSO_KEYCLOAK_SERVER_URL": "https://auth.example.org",
    "SSO_KEYCLOAK_REALM": "qgis",
    "SSO_PROVISIONER_CLIENT_ID": "feed-qgis-org-provisioner",
    "SSO_PROVISIONER_CLIENT_SECRET": "not-a-real-secret",
    "SSO_MAGIC_LINK_URL": "https://auth.example.org/realms/qgis/magic-link",
}

LINK = "https://auth.example.org/realms/qgis/login-actions/action-token?key=abc"


def response(status=200, payload=None, text=""):
    fake = mock.Mock()
    fake.status_code = status
    fake.json.return_value = payload if payload is not None else {}
    fake.text = text
    return fake


@override_settings(**CLIENT_SETTINGS)
class MagicLinkTest(TestCase):

    def setUp(self):
        self.client_ = KeycloakAdminClient()
        self.client_._token = "service-account-token"
        self.client_._token_expires_at = float("inf")

    def post(self, reply):
        with mock.patch.object(
            self.client_._session, "post", return_value=reply
        ) as posted:
            result = self.client_.magic_link(
                "alice",
                client_id="feed-qgis-org",
                redirect_uri="https://feed.example.org/oidc/authenticate/",
                lifespan=1209600,
            )
        return result, posted

    def test_the_link_and_its_subject_come_back(self):
        result, posted = self.post(
            response(payload={"user_id": "sub-1", "link": LINK, "sent": False})
        )

        self.assertEqual(result, ("sub-1", LINK))
        self.assertEqual(
            posted.call_args.args[0],
            "https://auth.example.org/realms/qgis/magic-link",
        )

    def test_the_three_dangerous_defaults_are_overridden(self):
        """None of these is the endpoint's default and each one matters.

        A reusable link is a standing credential rather than an invitation;
        force_create would have this site creating realm accounts as a side
        effect of asking for a link; and sending email is the other button.
        """
        _result, posted = self.post(
            response(payload={"user_id": "sub-1", "link": LINK})
        )

        body = posted.call_args.kwargs["json"]
        self.assertFalse(body["reusable"])
        self.assertFalse(body["force_create"])
        self.assertFalse(body["send_email"])

    def test_our_lifespan_is_sent_not_the_endpoint_default(self):
        """Its default is a day. A migration wave runs while people are away."""
        _result, posted = self.post(
            response(payload={"user_id": "sub-1", "link": LINK})
        )

        self.assertEqual(posted.call_args.kwargs["json"]["expiration_seconds"], 1209600)

    def test_the_service_account_token_is_sent(self):
        _result, posted = self.post(
            response(payload={"user_id": "sub-1", "link": LINK})
        )

        headers = posted.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer service-account-token")

    def test_a_failure_reports_the_status_and_not_the_body(self):
        """The body of a successful call is a credential; do not echo bodies."""
        with self.assertRaises(KeycloakError) as raised:
            self.post(response(status=403, text="secret-ish detail"))

        self.assertIn("403", str(raised.exception))
        self.assertNotIn("secret-ish", str(raised.exception))

    def test_a_reply_without_a_link_is_an_error(self):
        with self.assertRaises(KeycloakError):
            self.post(response(payload={"user_id": "sub-1", "sent": True}))

    def test_a_realm_without_the_extension_reports_the_404(self):
        """It answers on the realm path, so a missing extension is a 404 rather
        than a connection error."""
        with self.assertRaises(KeycloakError) as raised:
            self.post(response(status=404))

        self.assertIn("404", str(raised.exception))
