# coding=utf-8
"""The bounded window in which a token may claim a pre-existing account.

Linking is the only path by which a token reaches an account it was not
already bound to, so every one of these refusals matters more than the one
success.
"""

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
        """The fallback is for accounts that have not migrated, not for those
        that have. Leaving the password live would keep a second front door
        open with no 2FA and no central revocation."""
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
        """Ambiguity is a refusal, not a guess."""
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
        """Two accounts differing only in case are exactly the collision the
        export report exists to surface. Neither may be picked automatically."""
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
        """Otherwise a new realm account with the same name and address could
        take over an account already bound to somebody else's subject."""
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
        """The window is bounded by a flag that defaults to off."""
        self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(sub="sub-1", preferred_username="alice", email="alice@example.org")
        )

        self.assertIsNone(matched)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)
