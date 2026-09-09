# coding=utf-8
"""The admin actions that create realm accounts and invite people.

These replaced management commands, which were hard to reach and dry-run by
default. In a browser neither is true, so the guards had to be rebuilt: a
confirmation page, a cap on the selection, and a hard separation between
creating an account and emailing anybody. That is what these tests pin down.
"""

from unittest import mock

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import KeycloakIdentity, SsoAuditEvent
from ..provisioning import Provisioner
from .base import FakeRealm

CHANGELIST = reverse("admin:auth_user_changelist")

# force_login picks the first configured backend, which is the OIDC one, and
# SessionRefresh would then bounce every admin GET to Keycloak to renew a token
# these fixtures never had.
LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"

ADMIN_SETTINGS = {
    "OIDC_RP_CLIENT_ID": "feed-qgis-org",
    "SSO_SETUP_REDIRECT_URI": "https://feed.example.org/accounts/login/",
    "LOCAL_LOGIN_ENABLED": True,
    "AUTHENTICATION_BACKENDS": [
        "qgis_sso.auth.QGISOIDCAuthenticationBackend",
        "django.contrib.auth.backends.ModelBackend",
    ],
}


@override_settings(**ADMIN_SETTINGS)
class ProvisionActionTest(TestCase):

    def setUp(self):
        self.realm = FakeRealm()
        self.superuser = User.objects.create_superuser(
            "root", "root@example.org", "x", last_login=timezone.now()
        )
        self.target = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )
        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)

    def _patched(self, data):
        """Post the admin action with the realm stubbed out."""
        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        with mock.patch.object(Provisioner, "__init__", init):
            return self.client.post(CHANGELIST, data, follow=True)

    # -- the confirmation step --------------------------------------------

    def test_first_post_only_previews_and_writes_nothing(self):
        response = self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
            }
        )

        self.assertContains(response, "Will be created")
        self.assertContains(response, "alice@example.org")
        self.assertEqual(self.realm.created, [])
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_confirmed_post_creates_the_account_but_sends_nothing(self):
        """Inviting is a separate action, so it is never a side effect."""
        self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
                "confirm": "yes",
            }
        )

        self.assertEqual([u["username"] for u in self.realm.created], ["alice"])
        identity = KeycloakIdentity.objects.get(user=self.target)
        self.assertEqual(identity.sub, "sub-alice")
        self.assertEqual(self.realm.emailed, [])
        self.assertIsNone(identity.setup_email_sent_at)

    def test_created_account_is_passkey_only(self):
        """No password is ever set on the account, by us or by the user."""
        self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
                "confirm": "yes",
            }
        )

        payload = self.realm.created[0]
        self.assertNotIn("credentials", payload)
        self.assertFalse(payload["emailVerified"])
        self.assertIn("webauthn-register-passwordless", payload["requiredActions"])
        self.assertNotIn("UPDATE_PASSWORD", payload["requiredActions"])
        self.assertNotIn("CONFIGURE_TOTP", payload["requiredActions"])

    def test_provisioning_is_audited(self):
        self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
                "confirm": "yes",
            }
        )

        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.PROVISIONED, user=self.target
            ).exists()
        )

    # -- the guards --------------------------------------------------------

    def test_a_non_superuser_is_refused(self):
        """The service account can hand out roles on this site, so the action
        is superuser-only regardless of the change_user permission."""
        staff = User.objects.create_user(
            "bob", "bob@example.org", "x", is_staff=True, is_superuser=False
        )
        staff.user_permissions.add(
            Permission.objects.get(codename="change_user"),
            Permission.objects.get(codename="view_user"),
        )
        self.client.force_login(staff, backend=LOCAL_BACKEND)

        response = self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
                "confirm": "yes",
            }
        )

        self.assertEqual(self.realm.created, [])
        self.assertFalse(KeycloakIdentity.objects.exists())
        self.assertNotContains(response, "Will be created")

    @override_settings(SSO_ADMIN_ACTION_MAX_USERS=1)
    def test_oversized_selection_is_refused(self):
        other = User.objects.create_user("carol", "carol@example.org", "x")

        response = self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk), str(other.pk)],
                "confirm": "yes",
            }
        )

        self.assertContains(response, "smaller waves")
        self.assertEqual(self.realm.created, [])

    def test_an_already_linked_user_is_not_provisioned_twice(self):
        KeycloakIdentity.objects.create(
            user=self.target,
            sub="sub-existing",
            issuer="https://auth.example.org",
            link_method="pre-sso-migration",
        )

        response = self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
            }
        )

        self.assertContains(response, "already has a Keycloak identity")
        self.assertEqual(self.realm.created, [])

    def test_a_name_already_taken_in_the_realm_is_never_claimed(self):
        """It may belong to somebody else entirely."""
        self.realm.existing = {"alice"}

        response = self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk)],
                "confirm": "yes",
            }
        )

        self.assertContains(response, "not claiming it")
        self.assertEqual(self.realm.created, [])
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_flagged_accounts_are_held_back_unless_asked_for(self):
        dormant = User.objects.create_user("dave", "dave@example.org", "x")
        dormant.last_login = None
        dormant.save(update_fields=["last_login"])

        response = self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(dormant.pk)],
                "confirm": "yes",
            }
        )

        self.assertContains(response, "never-logged-in")
        self.assertEqual(self.realm.created, [])

    def test_one_failure_does_not_abandon_the_rest(self):
        self.realm.fail_on = {"alice"}
        carol = User.objects.create_user(
            "carol", "carol@example.org", "x", last_login=timezone.now()
        )

        self._patched(
            {
                "action": "provision_in_keycloak",
                "_selected_action": [str(self.target.pk), str(carol.pk)],
                "confirm": "yes",
            }
        )

        self.assertEqual([u["username"] for u in self.realm.created], ["carol"])
        self.assertTrue(KeycloakIdentity.objects.filter(user=carol).exists())
        self.assertFalse(KeycloakIdentity.objects.filter(user=self.target).exists())


@override_settings(**ADMIN_SETTINGS)
class SendSetupEmailActionTest(TestCase):
    """Sending is its own action so it can be repeated whenever needed."""

    def setUp(self):
        self.realm = FakeRealm()
        self.superuser = User.objects.create_superuser(
            "root", "root@example.org", "x", last_login=timezone.now()
        )
        self.target = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )
        self.identity = KeycloakIdentity.objects.create(
            user=self.target,
            sub="sub-alice",
            issuer="https://auth.example.org",
            link_method="pre-sso-migration",
        )
        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)

    def _send(self, user=None):
        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        if user is not None:
            self.client.force_login(user, backend=LOCAL_BACKEND)
        with mock.patch.object(Provisioner, "__init__", init):
            return self.client.post(
                reverse("admin:qgis_sso_keycloakidentity_changelist"),
                {
                    "action": "send_setup_email",
                    "_selected_action": [str(self.identity.pk)],
                },
                follow=True,
            )

    def test_sending_records_the_send(self):
        self._send()

        self.identity.refresh_from_db()
        self.assertEqual(self.realm.emailed, ["sub-alice"])
        self.assertIsNotNone(self.identity.setup_email_sent_at)
        self.assertEqual(self.identity.setup_email_send_count, 1)

    def test_sending_again_is_allowed_and_counted(self):
        """An expired link has no fallback on a passkey-only account."""
        self._send()
        self._send()

        self.identity.refresh_from_db()
        self.assertEqual(self.identity.setup_email_send_count, 2)

    def test_sending_is_audited(self):
        self._send()

        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.SETUP_EMAIL_SENT, user=self.target
            ).exists()
        )

    @override_settings(SSO_SETUP_REDIRECT_URI="")
    def test_missing_redirect_uri_stops_before_any_email(self):
        """Otherwise the mail goes out carrying a link Keycloak will reject."""
        response = self._send()

        self.assertContains(response, "SSO_SETUP_REDIRECT_URI")
        self.assertEqual(self.realm.emailed, [])

    def test_a_non_superuser_is_refused(self):
        staff = User.objects.create_user(
            "bob", "bob@example.org", "x", is_staff=True, is_superuser=False
        )
        staff.user_permissions.add(
            Permission.objects.get(codename="view_keycloakidentity"),
            Permission.objects.get(codename="change_keycloakidentity"),
        )

        self._send(user=staff)

        self.assertEqual(self.realm.emailed, [])

    def test_the_detail_page_offers_the_keycloak_console_fallback(self):
        """The setup link cannot be retrieved, so point at where it can be
        retried instead of implying we can hand it over."""
        response = self.client.get(
            reverse("admin:qgis_sso_keycloakidentity_change", args=[self.identity.pk])
        )

        self.assertContains(response, "Keycloak admin console")
        self.assertContains(response, "sub-alice")
        self.assertContains(response, "cannot be retrieved")
