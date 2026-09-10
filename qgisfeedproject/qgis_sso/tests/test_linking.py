# coding=utf-8
"""Binding a local account to one the realm already has.

The realm is shared with hub and plugins, so a long-standing contributor here
very likely already has a QGIS account. Provisioning refused those outright -
"it may belong to somebody else entirely" - which was right but left them with
no way onto SSO at all.

What makes a binding safe is the evidence: an exact address that Keycloak
reports as verified, and nothing else. A username is not evidence. Most of what
follows is the refusals, because that is where the risk is.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from ..models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from ..provisioning import Provisioner
from ..roles import mirror_roles
from .base import SSO_SETTINGS, FakeRealm

LINK_SETTINGS = dict(SSO_SETTINGS, OIDC_RP_CLIENT_ID="feed-qgis-org")


def realm_account(email, username="alice", sub="sub-realm", verified=True):
    return {
        "id": sub,
        "username": username,
        "email": email,
        "emailVerified": verified,
    }


@override_settings(**LINK_SETTINGS)
class LinkDecisionTest(TestCase):
    """What the realm has to say before anything is bound."""

    def setUp(self):
        for name in ("qgisfeedentry_authors", "qgisfeedentry_approver"):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create_user(
            "alice", "alice@example.org", "x", is_superuser=True, is_staff=True
        )

    def decide(self, realm):
        return Provisioner(client=realm).inspect(self.user)

    def test_a_verified_matching_address_can_be_linked(self):
        realm = FakeRealm(realm_users=[realm_account("alice@example.org")])

        decision = self.decide(realm)

        self.assertTrue(decision.is_link)
        self.assertEqual(decision.sub, "sub-realm")
        self.assertEqual(decision.roles, ["admin"])

    def test_an_unverified_address_is_refused_and_says_so(self):
        """They have not proved they control it, so it is not evidence."""
        realm = FakeRealm(
            realm_users=[realm_account("alice@example.org", verified=False)]
        )

        decision = self.decide(realm)

        self.assertTrue(decision.is_skip)
        self.assertIn("has not verified it", decision.reason)

    def test_an_address_the_realm_does_not_know_falls_through_to_creating(self):
        realm = FakeRealm()

        decision = self.decide(realm)

        self.assertTrue(decision.is_create)

    def test_a_realm_account_already_bound_here_is_refused(self):
        """Two local accounts on one subject would break every lookup in the
        app, all of which key on sub."""
        other = User.objects.create_user("bob", "bob@example.org", "x")
        KeycloakIdentity.objects.create(
            user=other,
            sub="sub-realm",
            issuer="https://auth.example.org/realms/qgis",
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )
        realm = FakeRealm(realm_users=[realm_account("alice@example.org")])

        decision = self.decide(realm)

        self.assertTrue(decision.is_skip)
        self.assertIn("already bound", decision.reason)

    def test_an_account_that_already_has_an_identity_is_refused(self):
        KeycloakIdentity.objects.create(
            user=self.user,
            sub="sub-existing",
            issuer="https://auth.example.org/realms/qgis",
            link_method=LinkMethod.PRE_SSO_MIGRATION,
        )
        realm = FakeRealm(realm_users=[realm_account("alice@example.org")])

        decision = self.decide(realm)

        self.assertTrue(decision.is_skip)
        self.assertIn("already has a Keycloak identity", decision.reason)

    def test_a_role_the_client_does_not_have_is_refused_not_crashed(self):
        """Linking assigns a role; it does not create one. Without this the
        assignment raises a KeyError on a realm where the roles are not
        deployed yet."""

        class NoRoles(FakeRealm):
            def client_roles(self, client_uuid):
                return {}

        realm = NoRoles(realm_users=[realm_account("alice@example.org")])

        decision = self.decide(realm)

        self.assertTrue(decision.is_error)
        self.assertIn("has no role(s) admin", decision.reason)

    def test_a_username_collision_alone_is_still_never_claimed(self):
        """The old refusal stands where there is no address to go on."""
        realm = FakeRealm(existing_usernames={"alice"})

        decision = self.decide(realm)

        self.assertTrue(decision.is_skip)
        self.assertIn("not claiming it", decision.reason)


@override_settings(**LINK_SETTINGS)
class LinkTest(TestCase):
    """Binding, and what it leaves behind."""

    def setUp(self):
        for name in ("qgisfeedentry_authors", "qgisfeedentry_approver"):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create_user(
            "alice", "alice@example.org", "x", is_superuser=True, is_staff=True
        )
        self.realm = FakeRealm(
            realm_users=[realm_account("alice@example.org", username="alice-qgis")]
        )
        self.provisioner = Provisioner(client=self.realm)

    def link(self):
        decision = self.provisioner.inspect(self.user)
        return self.provisioner.link(decision, sponsor=None)

    def test_it_creates_nothing_in_the_realm(self):
        self.link()

        self.assertEqual(self.realm.created, [])

    def test_it_binds_to_the_subject_the_realm_gave(self):
        identity = self.link()

        self.assertEqual(identity.sub, "sub-realm")
        self.assertEqual(identity.preferred_username, "alice-qgis")
        self.assertEqual(identity.email_at_link, "alice@example.org")

    def test_it_assigns_the_roles_the_account_already_implies(self):
        """Nothing is granted that was not already held."""
        self.link()

        self.assertEqual(self.realm.assigned, [("sub-realm", ["admin"])])

    def test_a_superuser_here_comes_back_a_superuser(self):
        """The round trip this whole exercise is for: linked, given the admin
        client role, and mirrored back to what they had."""
        self.link()
        self.user.is_superuser = False
        self.user.is_staff = False
        self.user.save(update_fields=["is_superuser", "is_staff"])

        mirror_roles(User.objects.get(pk=self.user.pk), ["admin"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)

    def test_a_realm_role_grants_nothing(self):
        """US-9.2. Superuser here comes from the admin client role on
        feed-qgis-org, never from a role held at realm level."""
        self.link()
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])

        mirror_roles(User.objects.get(pk=self.user.pk), ["qgis-superuser"])

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)

    def test_linking_is_audited_with_both_sides(self):
        self.link()

        event = SsoAuditEvent.objects.get(action=SsoAuditEvent.Action.LINKED)
        self.assertEqual(event.sub, "sub-realm")
        self.assertIn("alice@example.org", str(event.detail))
        self.assertIn("admin", str(event.detail))
