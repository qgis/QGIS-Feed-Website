# coding=utf-8
"""Which tokens get an account, and which do not."""

import time

from django.contrib.auth.models import User
from django.core.exceptions import SuspiciousOperation
from django.test import override_settings

from ..models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from .base import (
    CLIENT_ID,
    ISSUER,
    SSO_SETTINGS,
    SsoTestCase,
    id_token_payload,
    userinfo,
)


@override_settings(**SSO_SETTINGS)
class SubjectMatchingTest(SsoTestCase):
    """Users are resolved on 'sub' and on nothing else."""

    def test_returning_user_is_matched_by_sub(self):
        user = self.make_user()
        KeycloakIdentity.objects.create(
            user=user,
            sub="sub-1",
            issuer=ISSUER,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

        matched = self.authenticate(userinfo(sub="sub-1"))

        self.assertEqual(matched, user)

    def test_matched_after_username_and_email_both_change(self):
        """The whole point of keying on the subject.

        Someone who renames themselves in Keycloak and changes their address
        is still the same person, and must still reach the same account.
        """
        user = self.make_user(username="alice", email="alice@example.org")
        KeycloakIdentity.objects.create(
            user=user,
            sub="sub-1",
            issuer=ISSUER,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

        matched = self.authenticate(
            userinfo(
                sub="sub-1",
                preferred_username="alice-renamed",
                email="different@example.org",
            )
        )

        self.assertEqual(matched, user)

    def test_email_alone_never_matches(self):
        """A different subject with a known email gets nothing.

        This is the base class's default behaviour and the reason it is
        overridden: matching on email would let anyone able to set an address
        in the shared realm take over a site account.
        """
        self.make_user(username="alice", email="alice@example.org")

        matched = self.authenticate(
            userinfo(sub="a-different-subject", email="alice@example.org")
        )

        self.assertIsNone(matched)


@override_settings(**SSO_SETTINGS)
class PasswordRetirementTest(SsoTestCase):
    """A successful SSO login is the proof that retires the password.

    Provisioning alone proves nothing about whether the person can reach the
    account. Signing in does, and at that moment the local password is only a
    second way in with no second factor and no central revocation.
    """

    def link(self, user):
        return KeycloakIdentity.objects.create(
            user=user,
            sub="sub-1",
            issuer=ISSUER,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

    def test_first_sso_login_retires_the_local_password(self):
        user = self.make_user()
        identity = self.link(user)
        self.assertTrue(user.has_usable_password())

        self.authenticate(userinfo(sub="sub-1"))

        user.refresh_from_db()
        identity.refresh_from_db()
        self.assertFalse(user.has_usable_password())
        self.assertIsNotNone(identity.local_password_disabled_at)

    def test_retirement_is_audited(self):
        user = self.make_user()
        self.link(user)

        self.authenticate(userinfo(sub="sub-1"))

        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.LOCAL_PASSWORD_DISABLED, user=user
            ).exists()
        )

    @override_settings(**{**SSO_SETTINGS, "SSO_RETIRE_PASSWORD_ON_LOGIN": False})
    def test_the_behaviour_can_be_turned_off(self):
        """An escape hatch for a cutover that needs both doors open."""
        user = self.make_user()
        self.link(user)

        self.authenticate(userinfo(sub="sub-1"))

        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())


@override_settings(**SSO_SETTINGS)
class NoAutoCreateTest(SsoTestCase):
    """A valid realm user with no local account is refused, never created."""

    def test_unknown_subject_creates_no_user(self):
        before = User.objects.count()

        matched = self.authenticate(userinfo(sub="stranger"))

        self.assertIsNone(matched)
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)

    def test_refusal_is_audited(self):
        self.authenticate(userinfo(sub="stranger", preferred_username="stranger"))

        event = SsoAuditEvent.objects.get(action=SsoAuditEvent.Action.REFUSED)
        self.assertEqual(event.sub, "stranger")
        # The subject is recorded. The token never is.
        self.assertNotIn("access-token", str(event.detail))

    def test_create_user_refuses_even_if_called_directly(self):
        """OIDC_CREATE_USER is the braces; this is the belt.

        Flipping that setting by accident must not be enough to open the site
        to every user in the shared realm.
        """
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user(userinfo(sub="stranger"))


@override_settings(**SSO_SETTINGS)
class TokenVerificationTest(SsoTestCase):
    """Assertions mozilla-django-oidc does not make for us."""

    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        KeycloakIdentity.objects.create(
            user=self.user,
            sub="sub-1",
            issuer=ISSUER,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

    def test_foreign_audience_is_rejected(self):
        """A token minted for hub or plugins grants nothing here.

        Both clients live in the same realm, so without this check a token
        issued to one of them would authenticate against this site.
        """
        payload = id_token_payload(sub="sub-1", aud="hub-qgis-org")

        with self.assertRaises(SuspiciousOperation):
            self.authenticate(userinfo(sub="sub-1"), payload=payload)

    def test_foreign_issuer_is_rejected(self):
        payload = id_token_payload(
            sub="sub-1", iss="https://evil.example.org/realms/qgis"
        )

        with self.assertRaises(SuspiciousOperation):
            self.authenticate(userinfo(sub="sub-1"), payload=payload)

    def test_expired_token_is_rejected(self):
        payload = id_token_payload(sub="sub-1", exp=time.time() - 3600)

        with self.assertRaises(SuspiciousOperation):
            self.authenticate(userinfo(sub="sub-1"), payload=payload)

    def test_azp_must_be_this_client_when_audience_is_multiple(self):
        payload = id_token_payload(
            sub="sub-1", aud=[CLIENT_ID, "hub-qgis-org"], azp="hub-qgis-org"
        )

        with self.assertRaises(SuspiciousOperation):
            self.authenticate(userinfo(sub="sub-1"), payload=payload)

    def test_userinfo_subject_must_match_the_id_token(self):
        payload = id_token_payload(sub="sub-1")

        with self.assertRaises(SuspiciousOperation):
            self.authenticate(userinfo(sub="sub-2"), payload=payload)
