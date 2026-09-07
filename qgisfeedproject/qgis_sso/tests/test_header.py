# coding=utf-8
"""The account dropdown in the site header.

``QgisFeedUserVisitMiddleware`` substitutes a shared ``qgis_user`` account on
anonymous requests, so "is anyone signed in" is not a question ``request.user``
can answer. The header has always worked around that by name; these tests keep
the workaround honest.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import KeycloakIdentity

ACCOUNT_URL = "https://auth.example.org/realms/qgis/account/"

# force_login picks the first configured backend, which is the OIDC one, and
# SessionRefresh would then bounce every GET to Keycloak to renew a token this
# fixture never had. These tests are about the header, not the refresh.
LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"


@override_settings(SSO_ACCOUNT_URL=ACCOUNT_URL)
class HeaderAccountMenuTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.org", "x")

    def home(self):
        return self.client.get(reverse("all"))

    def test_anonymous_visitor_is_offered_login_not_a_username(self):
        response = self.home()

        self.assertContains(response, "Login")
        self.assertNotContains(response, "Log Out")
        self.assertNotContains(response, "qgis_user")

    def test_signed_in_user_sees_their_name_and_a_logout(self):
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

        response = self.home()

        self.assertContains(response, "alice")
        self.assertContains(response, "Log Out")

    def test_profile_is_offered_only_to_an_sso_linked_account(self):
        """A local-only account has nothing to manage at auth.qgis.org."""
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

        self.assertNotContains(self.home(), ACCOUNT_URL)

        KeycloakIdentity.objects.create(
            user=self.user, sub="sub-1", issuer="https://auth.example.org"
        )

        response = self.home()
        self.assertContains(response, ACCOUNT_URL)
        self.assertContains(response, "Profile")

    def test_logging_out_is_a_post(self):
        """A GET logout can be triggered by any image tag on any other site."""
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

        response = self.home()

        self.assertContains(response, f'action="{reverse("oidc_logout")}"')
        self.assertContains(response, "csrfmiddlewaretoken")
