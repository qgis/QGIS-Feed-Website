# coding=utf-8
"""Migration linking, the only way a token reaches an account it is not bound to."""

from django.test import override_settings

from ..models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from .base import SSO_SETTINGS, SsoTestCase, userinfo

LINKING_ON = {**SSO_SETTINGS, "SSO_MIGRATION_LINKING": True}
LINKING_OFF = {**SSO_SETTINGS, "SSO_MIGRATION_LINKING": False}


@override_settings(**LINKING_ON)
class LinkingEnabledTest(SsoTestCase):

    def test_exact_username_and_verified_email_links(self):
        user = self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(
                sub="sub-1",
                preferred_username="alice",
                email="alice@example.org",
                email_verified=True,
            )
        )

        self.assertEqual(matched, user)
        identity = KeycloakIdentity.objects.get(user=user)
        self.assertEqual(identity.sub, "sub-1")
        self.assertEqual(identity.link_method, LinkMethod.PRE_SSO_MIGRATION)
        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.LINKED, user=user
            ).exists()
        )

    def test_linking_makes_the_local_password_unusable(self):
        """A linked account signs in through Keycloak from now on."""
        user = self.make_user(username="alice", email="alice@example.org")
        self.assertTrue(user.has_usable_password())

        self.authenticate(
            userinfo(sub="sub-1", preferred_username="alice", email="alice@example.org")
        )

        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())

    def test_unverified_email_is_refused(self):
        self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(
                sub="sub-1",
                preferred_username="alice",
                email="alice@example.org",
                email_verified=False,
            )
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)

    def test_username_match_alone_is_refused(self):
        """A username match with a different address is refused."""
        self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(
                sub="sub-1", preferred_username="alice", email="someone@example.org"
            )
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)

    def test_email_match_alone_is_refused(self):
        self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(
                sub="sub-1", preferred_username="someone", email="alice@example.org"
            )
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)

    def test_ambiguous_match_is_refused(self):
        """Two accounts that differ only in case are both left alone."""
        self.make_user(username="alice", email="alice@example.org")
        self.make_user(username="Alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(sub="sub-1", preferred_username="alice", email="alice@example.org")
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)

    def test_inactive_account_is_not_linked(self):
        self.make_user(username="alice", email="alice@example.org", is_active=False)

        matched = self.authenticate(
            userinfo(sub="sub-1", preferred_username="alice", email="alice@example.org")
        )

        self.assertIsNone(matched)

    def test_already_linked_account_is_not_relinked_by_a_second_subject(self):
        """A second subject with the same name and address cannot take it over."""
        user = self.make_user(username="alice", email="alice@example.org")
        KeycloakIdentity.objects.create(
            user=user,
            sub="the-real-subject",
            issuer=SSO_SETTINGS["SSO_ISSUER"],
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

        matched = self.authenticate(
            userinfo(
                sub="an-impostor",
                preferred_username="alice",
                email="alice@example.org",
            )
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 1)


@override_settings(**LINKING_OFF)
class LinkingDisabledTest(SsoTestCase):

    def test_no_linking_when_the_flag_is_off(self):
        """Linking stays off unless the flag is on."""
        self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(sub="sub-1", preferred_username="alice", email="alice@example.org")
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)
