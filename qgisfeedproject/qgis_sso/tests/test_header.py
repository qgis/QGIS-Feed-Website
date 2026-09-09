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

    def test_profile_points_at_this_site_not_the_provider(self):
        """The page is here even though the passkeys are not.

        It was a link straight out to auth.qgis.org, which meant leaving the
        site to answer "what am I signed in as". A local-only account gets the
        page too; it explains there is nothing to manage there.
        """
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

        response = self.home()

        self.assertContains(response, reverse("qgis_sso:profile"))
        self.assertContains(response, "Profile")
        self.assertNotContains(response, ACCOUNT_URL)

    def test_the_enrolment_page_is_offered_to_superusers_only(self):
        """It writes to a realm shared with hub and plugins, so staff is not
        enough - the same rule the view itself applies."""
        enrolment = reverse("qgis_sso:enrolment")

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user, backend=LOCAL_BACKEND)
        self.assertNotContains(self.home(), enrolment)

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.assertContains(self.home(), enrolment)

    def test_an_anonymous_visitor_is_not_offered_it(self):
        self.assertNotContains(self.home(), reverse("qgis_sso:enrolment"))

    def test_logging_out_is_a_post(self):
        """A GET logout can be triggered by any image tag on any other site."""
        self.client.force_login(self.user, backend=LOCAL_BACKEND)

        response = self.home()

        self.assertContains(response, f'action="{reverse("oidc_logout")}"')
        self.assertContains(response, "csrfmiddlewaretoken")
