# coding=utf-8
"""The mapping from a Django account to a Keycloak one.

Which username it gets, which client roles its existing permissions imply,
which accounts a human has to decide about first, and when its local password
may be taken away. The admin actions are thin wrappers over these, so the
rules are tested here rather than through the interface.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from ..migration import (
    REPORT_FIELDS,
    flag_users,
    proposed_roles,
    proposed_username,
    report_row,
)
from ..models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from ..provisioning import (
    ALREADY_DISABLED,
    DISABLED,
    NOT_YET_PROVEN,
    disable_local_password,
)


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


class ReportRowTest(TestCase):
    """The row the CSV export writes, and what provisioning then acts on."""

    def test_row_carries_the_proposal_and_the_flags(self):
        user = User.objects.create_superuser("Root", "root@example.org", "x")

        row = report_row(user, flags=flag_users([user])[user.pk], linked_ids=set())

        self.assertEqual(row["proposed_keycloak_username"], "root")
        self.assertEqual(row["proposed_client_roles"], "admin")
        self.assertIn("never-logged-in", row["flags"])
        self.assertFalse(row["already_linked"])

    def test_every_declared_column_is_present(self):
        """The export writes with a DictWriter, so a missing key is an error."""
        user = User.objects.create_user("alice", "alice@example.org", "x")

        self.assertEqual(set(report_row(user)), set(REPORT_FIELDS))


class DisableLocalPasswordTest(TestCase):

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
        how people get locked out - and a passkey account has no reset."""
        self.assertEqual(disable_local_password(self.identity), NOT_YET_PROVEN)

        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())

    def test_password_is_disabled_after_a_recorded_sso_login(self):
        self.identity.first_sso_login_at = timezone.now()
        self.identity.save()

        self.assertEqual(disable_local_password(self.identity), DISABLED)

        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
        self.assertIsNotNone(self.identity.local_password_disabled_at)

    def test_an_account_that_already_had_none_is_recorded_not_repeated(self):
        self.identity.first_sso_login_at = timezone.now()
        self.identity.save()
        self.user.set_unusable_password()
        self.user.save(update_fields=["password"])

        self.assertEqual(disable_local_password(self.identity), ALREADY_DISABLED)

        self.identity.refresh_from_db()
        self.assertIsNotNone(self.identity.local_password_disabled_at)

    def test_disabling_is_audited(self):
        self.identity.first_sso_login_at = timezone.now()
        self.identity.save()

        disable_local_password(self.identity)

        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.LOCAL_PASSWORD_DISABLED,
                user=self.user,
            ).exists()
        )
