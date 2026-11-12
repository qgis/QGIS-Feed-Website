# coding=utf-8
"""The contributor's guide at /sso/help/, built from the role settings."""

from django.test import TestCase, override_settings
from django.urls import reverse

LABELS = {
    "admin": "Administrator",
    "reviewer": "Reviewer",
    "author": "Author",
}

SUMMARIES = {
    "admin": "Runs the site.",
    "reviewer": "Writes news items and publishes them.",
    "author": "Writes news items for a reviewer to publish.",
}

HELP_SETTINGS = {
    "SSO_ROLE_LABELS": LABELS,
    "SSO_ROLE_SUMMARIES": SUMMARIES,
}


@override_settings(**HELP_SETTINGS)
class HelpPageTest(TestCase):
    def visit(self):
        return self.client.get(reverse("qgis_sso:help"))

    def test_anybody_can_read_it(self):
        """People who cannot sign in need it most."""
        self.assertEqual(self.visit().status_code, 200)

    def test_it_names_every_role_the_site_declares(self):
        response = self.visit()

        for label in LABELS.values():
            self.assertContains(response, label)

    def test_it_says_what_each_role_does(self):
        response = self.visit()

        for summary in SUMMARIES.values():
            self.assertContains(response, summary)

    def test_it_never_shows_a_raw_role_identifier(self):
        """The Keycloak names mean nothing to a contributor."""
        self.assertNotContains(self.visit(), "usergroup-author")

    def test_it_answers_the_questions_it_promises(self):
        response = self.visit()

        for anchor in ("account", "permissions", "stuck"):
            self.assertContains(response, f'id="{anchor}"')


@override_settings(**HELP_SETTINGS)
class HelpIsReachableTest(TestCase):
    """The sign-in pages link to the guide."""

    def test_the_sign_in_page_links_to_it(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, reverse("qgis_sso:help"))

    def test_the_sign_in_failure_page_offers_it_as_help(self):
        response = self.client.get(reverse("qgis_sso:sign_in_failed"))

        self.assertContains(response, "/sso/help/", status_code=403)
