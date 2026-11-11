# coding=utf-8
"""Roles are mirrored in both directions, and only over managed groups.

The removal tests are the ones that matter. A mirror that only ever adds is
not a mirror, and a revocation in Keycloak that does not reach Django is a
permission that nobody can take away.
"""

from django.contrib.auth.models import Group
from django.test import override_settings

from ..models import KeycloakIdentity, LinkMethod
from ..roles import mirror_roles
from .base import ISSUER, SSO_SETTINGS, SsoTestCase, userinfo


@override_settings(**SSO_SETTINGS)
class RoleMirroringTest(SsoTestCase):

    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        KeycloakIdentity.objects.create(
            user=self.user,
            sub="sub-1",
            issuer=ISSUER,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

    def sign_in_with(self, roles):
        self.authenticate(userinfo(sub="sub-1", feed_roles=roles))
        self.user.refresh_from_db()
        return self.user

    def test_roles_grant_groups_and_flags(self):
        user = self.sign_in_with(["reviewer"])

        self.assertEqual(
            self.group_names(user),
            {"qgisfeedentry_authors", "qgisfeedentry_approver"},
        )
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_admin_role_grants_superuser(self):
        user = self.sign_in_with(["admin"])

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_removing_a_role_removes_the_groups_and_flags(self):
        self.sign_in_with(["admin"])

        user = self.sign_in_with(["author"])

        self.assertEqual(self.group_names(user), {"qgisfeedentry_authors"})
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_no_roles_yields_no_permissions(self):
        """Also the correct outcome for a hub or plugins user who wanders in
        with a token this site accepts but no feed roles on it."""
        self.sign_in_with(["admin"])

        user = self.sign_in_with([])

        self.assertEqual(self.group_names(user), set())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_absent_claim_yields_no_permissions(self):
        """A missing feed_roles claim is not the same as an error, and must
        not be treated as 'leave the existing permissions alone'."""
        self.sign_in_with(["admin"])

        claims = userinfo(sub="sub-1")
        claims.pop("feed_roles")
        self.authenticate(claims)
        self.user.refresh_from_db()

        self.assertEqual(self.group_names(self.user), set())
        self.assertFalse(self.user.is_superuser)

    def test_unmanaged_groups_are_untouched(self):
        """A group created by hand for an unrelated purpose survives a
        sign-in, in both directions."""
        unrelated = Group.objects.create(name="mailing-list-moderators")
        self.user.groups.add(unrelated)

        user = self.sign_in_with(["author"])

        self.assertIn("mailing-list-moderators", self.group_names(user))

    def test_unknown_roles_are_ignored(self):
        """The realm is shared, so a token may legitimately carry roles that
        mean nothing here."""
        user = self.sign_in_with(["author", "some-hub-role"])

        self.assertEqual(self.group_names(user), {"qgisfeedentry_authors"})

    def test_last_seen_roles_are_recorded_for_diagnostics(self):
        self.sign_in_with(["reviewer"])

        identity = KeycloakIdentity.objects.get(sub="sub-1")
        self.assertEqual(identity.last_seen_roles, ["reviewer"])
        self.assertIsNotNone(identity.first_sso_login_at)


@override_settings(**SSO_SETTINGS)
class SignalInteractionTest(SsoTestCase):
    """qgisfeed's setup_group receiver must not undo a revocation.

    It fires on every User save, including the last_login write that
    django.contrib.auth.login performs *after* authentication returns, so an
    unguarded receiver would silently re-grant the authors group to any staff
    user immediately after the mirror had removed it.
    """

    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        KeycloakIdentity.objects.create(
            user=self.user,
            sub="sub-1",
            issuer=ISSUER,
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )

    def test_saving_a_linked_staff_user_does_not_re_add_the_authors_group(self):
        self.user.is_staff = True
        self.user.save()
        self.user.groups.clear()

        # Stands in for the last_login write that follows authentication.
        self.user.save()

        self.assertNotIn("qgisfeedentry_authors", self.group_names(self.user))

    def test_another_users_save_does_not_re_add_groups(self):
        """The receiver used to sweep every staff user on any save, so one
        person signing in restored another person's revoked group."""
        mirror_roles(self.user, ["author"])
        mirror_roles(self.user, [])
        self.user.refresh_from_db()

        other = self.make_user(username="bob", email="bob@example.org", is_staff=True)
        other.save()

        self.assertEqual(self.group_names(self.user), set())

    def test_unlinked_staff_users_still_get_the_group(self):
        """The original behaviour, preserved for accounts that are not
        SSO-linked - qgisfeed's own test suite depends on it."""
        local = self.make_user(username="carol", email="carol@example.org")
        local.is_staff = True
        local.save()

        self.assertIn("qgisfeedentry_authors", self.group_names(local))
