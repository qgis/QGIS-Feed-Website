# coding=utf-8
"""The migration commands, with the email allowlist front and centre.

``sso_send_setup_links`` is the one command in the migration whose mistakes
cannot be undone, so its guard is tested as behaviour rather than trusted as
a convention.
"""

import io

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ..management.commands.sso_send_setup_links import is_allowed
from ..migration import flag_users, proposed_roles, proposed_username
from ..models import KeycloakIdentity, LinkMethod


class AllowlistTest(TestCase):

    def test_empty_allowlist_matches_nothing(self):
        """Fails closed. An unset allowlist must send nothing, not everything -
        a staging database restored from production holds every contributor's
        real address."""
        self.assertFalse(is_allowed("alice@example.org", []))

    def test_exact_address_matches(self):
        self.assertTrue(is_allowed("alice@example.org", ["alice@example.org"]))

    def test_glob_matches_a_domain(self):
        self.assertTrue(is_allowed("alice@kartoza.com", ["*@kartoza.com"]))

    def test_non_matching_address_is_refused(self):
        self.assertFalse(is_allowed("alice@example.org", ["*@kartoza.com"]))

    def test_blank_address_is_refused(self):
        self.assertFalse(is_allowed("", ["*"]))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(is_allowed("Alice@Example.ORG", ["alice@example.org"]))

    @override_settings(SSO_SETUP_EMAIL_ALLOWLIST=[])
    def test_command_refuses_to_run_with_an_empty_allowlist(self):
        user = User.objects.create_user("alice", "alice@example.org", "x")
        KeycloakIdentity.objects.create(
            user=user,
            sub="sub-1",
            issuer="https://auth.example.org/realms/qgis",
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

        with self.assertRaises(CommandError) as caught:
            call_command("sso_send_setup_links", stdout=io.StringIO())

        self.assertIn("SSO_SETUP_EMAIL_ALLOWLIST", str(caught.exception))


class ProposedMappingTest(TestCase):
    """What the export report promises is what provisioning acts on."""

    def test_username_is_lowercased_for_keycloak(self):
        user = User.objects.create_user("Alice", "alice@example.org", "x")

        self.assertEqual(proposed_username(user), "alice")

    def test_superuser_proposes_admin(self):
        user = User.objects.create_superuser("root", "root@example.org", "x")

        self.assertEqual(proposed_roles(user), ["admin"])

    def test_approver_group_proposes_reviewer(self):
        user = User.objects.create_user("bob", "bob@example.org", "x")
        group, _ = Group.objects.get_or_create(name="qgisfeedentry_approver")
        user.groups.add(group)

        self.assertEqual(proposed_roles(User.objects.get(pk=user.pk)), ["reviewer"])

    def test_staff_proposes_author(self):
        user = User.objects.create_user(
            "carol", "carol@example.org", "x", is_staff=True
        )

        self.assertEqual(proposed_roles(user), ["author"])

    def test_ordinary_user_proposes_nothing(self):
        user = User.objects.create_user("dave", "dave@example.org", "x")
        user.groups.clear()

        self.assertEqual(proposed_roles(User.objects.get(pk=user.pk)), [])


class FlaggingTest(TestCase):
    """Every flag means a human has to decide before anything is created."""

    def test_case_collision_is_flagged_on_both_accounts(self):
        alice = User.objects.create_user("alice", "a@example.org", "x")
        alice_upper = User.objects.create_user("Alice", "b@example.org", "x")

        flags = flag_users([alice, alice_upper])

        self.assertIn("username-case-collision", flags[alice.pk])
        self.assertIn("username-case-collision", flags[alice_upper.pk])

    def test_missing_email_is_flagged(self):
        """No address means no setup link, so no unattended migration path."""
        user = User.objects.create_user("alice", "", "x")

        self.assertIn("no-email", flag_users([user])[user.pk])

    def test_duplicate_email_is_flagged(self):
        one = User.objects.create_user("alice", "shared@example.org", "x")
        two = User.objects.create_user("bob", "shared@example.org", "x")

        flags = flag_users([one, two])

        self.assertIn("duplicate-email", flags[one.pk])
        self.assertIn("duplicate-email", flags[two.pk])

    def test_never_logged_in_is_flagged(self):
        user = User.objects.create_user("alice", "a@example.org", "x")

        self.assertIn("never-logged-in", flag_users([user])[user.pk])

    def test_inactive_is_flagged(self):
        user = User.objects.create_user("alice", "a@example.org", "x", is_active=False)

        self.assertIn("inactive", flag_users([user])[user.pk])


class ExportCommandTest(TestCase):

    def test_export_writes_a_row_per_user_and_changes_nothing(self):
        User.objects.create_user("alice", "alice@example.org", "x")
        before = User.objects.count()
        out = io.StringIO()

        call_command("sso_export_users", stdout=out, stderr=io.StringIO())

        self.assertIn("alice", out.getvalue())
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(KeycloakIdentity.objects.count(), 0)


class DisableLocalPasswordsTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.org", "x")
        self.identity = KeycloakIdentity.objects.create(
            user=self.user,
            sub="sub-1",
            issuer="https://auth.example.org/realms/qgis",
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

    def test_provisioned_but_never_signed_in_is_left_alone(self):
        """Taking the password before SSO is proven to work for this person is
        how people get locked out."""
        call_command("sso_disable_local_passwords", "--commit", stdout=io.StringIO())

        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())

    def test_password_is_disabled_after_a_recorded_sso_login(self):
        from django.utils import timezone

        self.identity.first_sso_login_at = timezone.now()
        self.identity.save()

        call_command("sso_disable_local_passwords", "--commit", stdout=io.StringIO())

        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_dry_run_changes_nothing(self):
        from django.utils import timezone

        self.identity.first_sso_login_at = timezone.now()
        self.identity.save()

        call_command("sso_disable_local_passwords", stdout=io.StringIO())

        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())
