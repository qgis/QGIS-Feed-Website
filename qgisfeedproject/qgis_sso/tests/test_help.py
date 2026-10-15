# coding=utf-8
"""The contributor's guide at /sso/help/.

The page is generated from the same settings the rules are enforced from, so
these tests pin that relationship rather than the wording: a ladder declared
under ``override_settings`` has to be the ladder the page describes.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

LADDER = {
    "admin": 0,
    "reviewer": 2,
    "author": 4,
}

LABELS = {
    "admin": "Administrator",
    "reviewer": "Reviewer",
    "author": "Author",
}

SUMMARIES = {
    "admin": "Runs the site, and can invite anybody.",
    "reviewer": "Writes news items and publishes them.",
    "author": "Writes news items for a reviewer to publish.",
}

QUOTAS = {0: None, 2: 10, 4: 3}

HELP_SETTINGS = {
    "SSO_ROLE_TIERS": LADDER,
    "SSO_ROLE_LABELS": LABELS,
    "SSO_ROLE_SUMMARIES": SUMMARIES,
    "SSO_TIER_QUOTAS": QUOTAS,
    "SSO_REVOCATION_GRACE_DAYS": 7,
}


@override_settings(**HELP_SETTINGS)
class HelpPageTest(TestCase):
    def visit(self):
        return self.client.get(reverse("qgis_sso:help"))

    def test_anybody_can_read_it(self):
        """Somebody locked out is exactly who needs it, so there is no login."""
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

    def test_it_states_the_invitation_allowance(self):
        self.assertContains(self.visit(), "3 at a time")

    def test_an_unlimited_allowance_is_not_shown_as_a_number(self):
        self.assertContains(self.visit(), "No limit")

    def test_it_says_who_each_role_may_invite(self):
        """A reviewer sits above authors, so both appear on its line."""
        self.assertContains(self.visit(), "Reviewer can invite: Reviewer, Author")

    def test_the_grace_period_comes_from_settings(self):
        with self.settings(SSO_REVOCATION_GRACE_DAYS=3):
            self.assertContains(self.visit(), "3 days")

    def test_it_answers_the_questions_it_promises(self):
        response = self.visit()

        for anchor in ("passkeys", "trust", "suspended", "stuck", "link"):
            self.assertContains(response, f'id="{anchor}"')


@override_settings(**HELP_SETTINGS)
class HelpIsReachableTest(TestCase):
    """The guide is worth nothing if nobody stumbles across it."""

    def test_the_sign_in_page_links_to_it(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, reverse("qgis_sso:help"))

    def test_the_sign_in_failure_page_offers_it_as_help(self):
        response = self.client.get(reverse("qgis_sso:sign_in_failed"))

        self.assertContains(response, "/sso/help/", status_code=403)
