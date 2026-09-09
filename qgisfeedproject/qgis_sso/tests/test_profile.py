# coding=utf-8
"""The profile page and the passkeys on it.

The page is on this site; the passkeys are not, and cannot be. WebAuthn binds
a credential to the relying party that created it, so this site can only list
them and hand the user to Keycloak to change one. These pin down that it never
tries to do more than that, and that it cannot be talked into acting on
somebody else's credential.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import KeycloakIdentity
from ..passkeys import ACTION_SESSION_KEY

PROFILE = reverse("qgis_sso:profile")

LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"

PROFILE_SETTINGS = {
    "SSO_ACCOUNT_URL": "https://auth.example.org/realms/qgis/account/",
    "LOCAL_LOGIN_ENABLED": True,
    "AUTHENTICATION_BACKENDS": [
        "qgis_sso.auth.QGISOIDCAuthenticationBackend",
        "django.contrib.auth.backends.ModelBackend",
    ],
}


def credential(id="cred-1", label="Phone", type="webauthn-passwordless"):
    return {"id": id, "userLabel": label, "type": type, "createdDate": 1700000000000}


class FakeRealm:
    """Answers the one read the profile page makes, and refuses anything else.

    Being strict is the point: it is what proves the page never writes to the
    realm. Every credential change is authorised by the user at Keycloak, so a
    hole in this page cannot spend their credentials.
    """

    def __init__(self, credentials=()):
        self.credentials = list(credentials)
        self.asked = []

    def user_credentials(self, user_id):
        self.asked.append(user_id)
        return self.credentials

    def __getattr__(self, name):
        raise AssertionError(f"the profile page called {name} on the realm")


@override_settings(**PROFILE_SETTINGS)
class ProfileTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.org", "x")
        self.identity = KeycloakIdentity.objects.create(
            user=self.user,
            sub="sub-alice",
            issuer="https://auth.example.org/realms/qgis",
            link_method="pre-sso-migration",
        )
        self.realm = FakeRealm([credential()])
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

    def visit(self, method="get", **data):
        with mock.patch(
            "qgis_sso.passkeys.KeycloakAdminClient", return_value=self.realm
        ):
            if method == "post":
                return self.client.post(PROFILE, data)
            return self.client.get(PROFILE)

    # -- what it shows -----------------------------------------------------

    def test_it_lists_the_passkeys_on_the_account(self):
        response = self.visit()

        self.assertContains(response, "Phone")
        self.assertEqual(self.realm.asked, ["sub-alice"])

    def test_it_asks_only_about_the_signed_in_account(self):
        """The subject comes from the session's own identity, never a request."""
        KeycloakIdentity.objects.create(
            user=User.objects.create_user("bob", "bob@example.org", "x"),
            sub="sub-bob",
            issuer="https://auth.example.org/realms/qgis",
            link_method="pre-sso-migration",
        )

        self.visit()

        self.assertEqual(self.realm.asked, ["sub-alice"])

    def test_a_password_credential_is_not_shown_as_a_passkey(self):
        self.realm.credentials = [credential(type="password", label="Old password")]

        response = self.visit()

        self.assertNotContains(response, "Old password")

    def test_an_unreachable_realm_still_renders_the_page(self):
        """Losing the provider for a moment is not the reader's problem."""
        from ..keycloak import KeycloakError

        self.realm.user_credentials = mock.Mock(side_effect=KeycloakError("down"))

        response = self.visit()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not be reached")

    def test_a_local_only_account_is_told_there_is_nothing_to_manage(self):
        self.identity.delete()

        response = self.visit()

        self.assertContains(response, "does not sign in through auth.qgis.org")
        # And is not sent to an account console it has no account in.
        self.assertNotContains(response, "Edit profile")

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        """QgisFeedUserVisitMiddleware substitutes a shared account on
        anonymous requests, so this is read from the session."""
        self.client.logout()

        response = self.client.get(PROFILE)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    # -- handing over to Keycloak ------------------------------------------

    def test_adding_a_passkey_starts_a_sign_in_carrying_the_action(self):
        response = self.visit("post", action="add-passkey")

        self.assertEqual(
            self.client.session[ACTION_SESSION_KEY], "webauthn-register-passwordless"
        )
        self.assertIn(reverse("oidc_authentication_init"), response["Location"])

    def test_removing_a_passkey_names_the_credential(self):
        self.realm.credentials = [credential(), credential(id="cred-2", label="Key")]

        self.visit("post", action="remove-passkey", credential="cred-2")

        self.assertEqual(
            self.client.session[ACTION_SESSION_KEY], "delete_credential:cred-2"
        )

    def test_a_credential_that_is_not_theirs_is_refused(self):
        """Keycloak would refuse it too, but only after being told about it."""
        response = self.visit(
            "post", action="remove-passkey", credential="somebody-elses"
        )

        self.assertNotIn(ACTION_SESSION_KEY, self.client.session)
        self.assertRedirects(response, PROFILE, fetch_redirect_response=False)

    def test_the_last_passkey_cannot_be_removed(self):
        """These accounts have no password, and the setup link an administrator
        could send is refused for anybody who has already signed in."""
        self.visit("post", action="remove-passkey", credential="cred-1")

        self.assertNotIn(ACTION_SESSION_KEY, self.client.session)


@override_settings(**PROFILE_SETTINGS)
class ActionRequestTest(TestCase):
    """The sign-in request only carries an action the site put there itself."""

    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.org", "x")
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

    def test_a_query_parameter_cannot_choose_the_action(self):
        """Otherwise a link could send somebody to Keycloak to have any action
        at all run against their account."""
        response = self.client.get(
            reverse("oidc_authentication_init") + "?kc_action=delete_account"
        )

        self.assertNotIn("kc_action", response["Location"])

    def test_the_action_is_spent_once_used(self):
        session = self.client.session
        session[ACTION_SESSION_KEY] = "webauthn-register-passwordless"
        session.save()

        first = self.client.get(reverse("oidc_authentication_init"))
        self.assertIn("kc_action=webauthn-register-passwordless", first["Location"])

        second = self.client.get(reverse("oidc_authentication_init"))
        self.assertNotIn("kc_action", second["Location"])
