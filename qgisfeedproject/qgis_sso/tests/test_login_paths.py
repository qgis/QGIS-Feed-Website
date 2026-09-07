# coding=utf-8
"""LOCAL_LOGIN_ENABLED: one flag, three effects.

The flag has to gate the backend, the form and Django admin's own login view
together. Gating only the form would leave the fallback wider than it looks,
which is exactly the failure the migration plan is guarding against.
"""

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import SsoAuditEvent

OIDC_ONLY = ["qgis_sso.auth.QGISOIDCAuthenticationBackend"]
WITH_LOCAL = OIDC_ONLY + ["django.contrib.auth.backends.ModelBackend"]


class LoginPageTest(TestCase):
    """force_login here deliberately leaves the OIDC backend in the session.

    That is what SessionRefresh looks at, so these also pin the login page
    being exempt from it: without the exemption every case below is a 302 to
    Keycloak instead of the page under test.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        self.user = User.objects.create_user(
            username="alice", email="alice@example.org", password="correct horse"
        )

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_both_ways_in_are_offered(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Login with QGIS account")
        self.assertContains(response, 'name="password"')

    @override_settings(LOCAL_LOGIN_ENABLED=False, AUTHENTICATION_BACKENDS=OIDC_ONLY)
    def test_local_form_is_absent_when_disabled(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Login with QGIS account")
        self.assertNotContains(response, 'name="password"')

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_signed_in_user_bounced_by_a_permission_check_is_told_why(self):
        """Otherwise the login form is shown to somebody already signed in."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("login"), {"next": "/manage/"})

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "do not have access", status_code=403)

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_signed_in_user_visiting_the_page_directly_is_sent_on(self):
        """Keycloak returns a newly enrolled user here already signed in.

        Showing them the form again looks like a failure and costs a second
        round trip through the provider before anything happens.
        """
        self.client.force_login(self.user)

        response = self.client.get(reverse("login"))

        self.assertRedirects(
            response, settings.LOGIN_REDIRECT_URL, fetch_redirect_response=False
        )

    @override_settings(LOCAL_LOGIN_ENABLED=False, AUTHENTICATION_BACKENDS=OIDC_ONLY)
    def test_local_password_is_refused_when_disabled(self):
        """Hiding the form is cosmetic; removing the backend is the control."""
        logged_in = self.client.login(username="alice", password="correct horse")

        self.assertFalse(logged_in)

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_local_login_is_audited(self):
        self.client.login(username="alice", password="correct horse")

        event = SsoAuditEvent.objects.get(action=SsoAuditEvent.Action.LOCAL_LOGIN)
        self.assertEqual(event.username, "alice")


class AdminLoginTest(TestCase):
    """Django admin ships a second login view. It must not be a second door."""

    def test_admin_login_redirects_to_the_site_login_page(self):
        response = self.client.get("/admin/login/")

        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_next_is_preserved_for_same_site_destinations(self):
        response = self.client.get("/admin/login/?next=/admin/qgisfeed/")

        self.assertIn("next=%2Fadmin%2Fqgisfeed%2F", response["Location"])

    def test_offsite_next_is_dropped(self):
        """Otherwise the redirect is an open redirect wearing a login page."""
        response = self.client.get("/admin/login/?next=https://evil.example.org/")

        self.assertNotIn("evil.example.org", response["Location"])


class SignInFailedPageTest(TestCase):

    def test_refusal_page_explains_the_invitation_requirement(self):
        session = self.client.session
        session["qgis_sso_refusal"] = "no-account"
        session.save()

        response = self.client.get(reverse("qgis_sso:sign_in_failed"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "You need an invitation", status_code=403)

    def test_generic_message_without_a_recorded_reason(self):
        """Do not assert a reason we cannot actually establish."""
        response = self.client.get(reverse("qgis_sso:sign_in_failed"))

        self.assertContains(response, "We could not sign you in", status_code=403)
