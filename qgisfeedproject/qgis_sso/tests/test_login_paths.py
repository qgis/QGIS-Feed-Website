# coding=utf-8
"""LOCAL_LOGIN_ENABLED: one flag, three effects.

The flag has to gate the backend, the form and Django admin's own login view
together. Gating only the form would leave the fallback wider than it looks,
which is exactly the failure the migration plan is guarding against.
"""

from html.parser import HTMLParser

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import SsoAuditEvent

OIDC_ONLY = ["qgis_sso.auth.QGISOIDCAuthenticationBackend"]
WITH_LOCAL = OIDC_ONLY + ["django.contrib.auth.backends.ModelBackend"]


class FormOwnership(HTMLParser):
    """Which form each named control ends up in, the way a browser sees it.

    HTML has no nested forms. A second ``<form>`` inside an open one is dropped
    by the parser, and every control under it is submitted by the outer form
    instead, to the outer form's action and by its method. That is invisible in
    the template and in any assertion that only searches the markup for a
    string, so the page is parsed here instead.
    """

    def __init__(self):
        super().__init__()
        self.open_forms = []
        #: Every form the browser keeps, as (method, action).
        self.forms = []
        #: control name -> (method, action) of the form that will submit it.
        self.owners = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            if self.open_forms:
                # What the browser does with it, so the test sees it too.
                return
            form = (
                (attributes.get("method") or "get").lower(),
                attributes.get("action", ""),
            )
            self.open_forms.append(form)
            self.forms.append(form)
        elif tag in ("input", "button", "select", "textarea") and self.open_forms:
            name = attributes.get("name") or attributes.get("id")
            if name:
                self.owners[name] = self.open_forms[-1]

    def handle_endtag(self, tag):
        if tag == "form" and self.open_forms:
            self.open_forms.pop()

    @classmethod
    def read(cls, response):
        parser = cls()
        parser.feed(response.content.decode())
        return parser


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

        self.assertContains(response, "Sign in with your QGIS account")
        self.assertContains(response, 'name="password"')

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_the_password_form_posts_here_and_not_to_keycloak(self):
        """The two ways in are two forms, and HTML cannot nest them.

        While the QGIS form stayed open around the password fields, the parser
        dropped the inner form and the Sign in button submitted the outer one
        instead: a GET to the provider, so every attempt at a local password
        landed on the Keycloak login page.
        """
        page = FormOwnership.read(self.client.get(reverse("login")))

        # An empty action is this page, which is where LoginView reads the
        # credentials from.
        self.assertEqual(page.owners["username"], ("post", ""))
        self.assertEqual(page.owners["password"], ("post", ""))
        self.assertEqual(page.owners["submit-button"], ("post", ""))

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_the_qgis_button_still_reaches_the_provider(self):
        page = FormOwnership.read(self.client.get(reverse("login")))

        self.assertIn(("get", reverse("oidc_authentication_init")), page.forms)

    @override_settings(LOCAL_LOGIN_ENABLED=True, AUTHENTICATION_BACKENDS=WITH_LOCAL)
    def test_a_local_password_signs_somebody_in_from_the_page(self):
        """End to end through the form, not through client.login."""
        response = self.client.post(
            reverse("login"),
            {"username": "alice", "password": "correct horse"},
        )

        self.assertRedirects(
            response, settings.LOGIN_REDIRECT_URL, fetch_redirect_response=False
        )

    @override_settings(LOCAL_LOGIN_ENABLED=False, AUTHENTICATION_BACKENDS=OIDC_ONLY)
    def test_local_form_is_absent_when_disabled(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Sign in with your QGIS account")
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
