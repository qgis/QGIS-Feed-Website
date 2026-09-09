# coding=utf-8
"""Inviting somebody, and the ladder that limits who may invite whom.

The account is created at the moment of invitation rather than by a token
redeemed later, so there is no public endpoint here and no unbound link: the
inviter types the address. What is left to pin down is the ladder, the quota,
and that nobody can reach past what their own tier allows.

Every invariant from the web-of-trust document that this slice implements gets
a named test, per US-8.3. Revocation cascades and re-parenting are not built and
are not tested.
"""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import tiers
from ..models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from ..provisioning import Provisioner
from .base import FakeRealm

MANAGE = reverse("qgis_sso:enrolment")
INVITE_NEW = reverse("qgis_sso:invite_new")
INVITE_EXISTING = reverse("qgis_sso:invite_existing")

LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"

LADDER = {
    "admin": 0,
    "web-maintainer": 1,
    "reviewer": 2,
    "usergroup-author": 3,
    "author": 4,
}

TRUST_SETTINGS = {
    "SSO_ROLE_TIERS": LADDER,
    "SSO_TIER_QUOTAS": {0: None, 1: 25, 2: 10, 3: 10, 4: 3},
    "OIDC_RP_CLIENT_ID": "feed-qgis-org",
    "SSO_SETUP_REDIRECT_URI": "https://feed.example.org/oidc/authenticate/",
    "SSO_MAGIC_LINK_URL": "https://auth.example.org/realms/qgis/magic-link",
    "LOCAL_LOGIN_ENABLED": True,
    "AUTHENTICATION_BACKENDS": [
        "qgis_sso.auth.QGISOIDCAuthenticationBackend",
        "django.contrib.auth.backends.ModelBackend",
    ],
}


class InvitingRealm(FakeRealm):
    """The provisioning double, plus the address lookup inviting makes."""

    def __init__(self, *args, existing_emails=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_emails = set(existing_emails)

    def find_user_by_email(self, email):
        return {"id": "somebody"} if email in self.existing_emails else None


def contributor(username, roles, sponsor=None, **kwargs):
    """A user with an identity carrying the given Keycloak roles."""
    user = User.objects.create_user(username, f"{username}@example.org", "x", **kwargs)
    KeycloakIdentity.objects.create(
        user=user,
        sub=f"sub-{username}",
        issuer="https://auth.example.org/realms/qgis",
        link_method=(
            LinkMethod.INVITATION if sponsor else LinkMethod.PRE_SSO_MIGRATION
        ),
        last_seen_roles=list(roles),
        sponsor=sponsor,
        is_root=sponsor is None,
    )
    return user


@override_settings(**TRUST_SETTINGS)
class TierTest(TestCase):
    """Effective tier, quota, and what each may offer."""

    def test_the_most_privileged_role_held_decides(self):
        """A person may hold several roles and the highest wins."""
        both = contributor("chris", ["author", "web-maintainer"])

        self.assertEqual(tiers.effective_tier(both), 1)

    def test_quota_is_the_largest_allowance_not_the_sum(self):
        """Holding two roles is not a reason to invite twice as many people."""
        both = contributor("chris", ["author", "reviewer"])

        self.assertEqual(tiers.quota(both), 10)

    def test_an_account_with_no_known_role_may_invite_nobody(self):
        nobody = contributor("dana", ["some-other-client-role"])

        self.assertEqual(tiers.invitable_roles(nobody), [])
        self.assertEqual(tiers.quota(nobody), 0)

    def test_an_account_with_no_identity_may_invite_nobody(self):
        local = User.objects.create_user("local", "local@example.org", "x")

        self.assertEqual(tiers.invitable_roles(local), [])

    def test_nobody_may_offer_a_role_above_their_own(self):
        """Invariant 3, and the reason the graph cannot be climbed."""
        reviewer = contributor("rita", ["reviewer"])

        self.assertEqual(
            tiers.invitable_roles(reviewer),
            ["reviewer", "usergroup-author", "author"],
        )
        self.assertFalse(tiers.may_invite(reviewer, "admin"))
        self.assertFalse(tiers.may_invite(reviewer, "web-maintainer"))

    def test_an_author_may_only_invite_authors(self):
        author = contributor("ali", ["author"])

        self.assertEqual(tiers.invitable_roles(author), ["author"])

    @override_settings(SSO_TIER_QUOTAS={0: None, 1: 25, 2: 2, 3: 10, 4: 3})
    def test_the_quota_counts_people_who_have_not_arrived(self):
        """Invariant 6: the forest cannot expand without bound.

        An account whose holder has never signed in is a place still being held
        for them - the same thing an outstanding invitation used to mean.
        """
        reviewer = contributor("rita", ["reviewer"])
        contributor("one", ["author"], sponsor=reviewer)

        self.assertEqual(tiers.remaining(reviewer), 1)

    @override_settings(SSO_TIER_QUOTAS={0: None, 1: 25, 2: 1, 3: 10, 4: 3})
    def test_a_place_frees_up_once_they_sign_in(self):
        reviewer = contributor("rita", ["reviewer"])
        invitee = contributor("one", ["author"], sponsor=reviewer)
        self.assertEqual(tiers.remaining(reviewer), 0)

        identity = invitee.keycloak_identity
        identity.first_sso_login_at = timezone.now()
        identity.save(update_fields=["first_sso_login_at"])

        self.assertEqual(tiers.remaining(reviewer), 1)

    def test_an_administrator_has_no_ceiling(self):
        root = contributor("root", ["admin"])

        self.assertIsNone(tiers.remaining(root))


@override_settings(**TRUST_SETTINGS)
class ScopingTest(TestCase):
    """Who sees what on the list, and who may act on it."""

    def setUp(self):
        self.realm = InvitingRealm()
        self.superuser = User.objects.create_superuser("root", "root@example.org", "x")
        KeycloakIdentity.objects.create(
            user=self.superuser,
            sub="sub-root",
            issuer="https://auth.example.org/realms/qgis",
            link_method=LinkMethod.PRE_SSO_MIGRATION,
            last_seen_roles=["admin"],
            is_root=True,
        )
        self.reviewer = contributor("rita", ["reviewer"])
        self.mine = contributor("mine", ["author"], sponsor=self.reviewer)
        self.theirs = contributor("theirs", ["author"], sponsor=self.superuser)

    def test_a_superuser_sees_every_account(self):
        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)

        response = self.client.get(MANAGE)

        self.assertContains(response, "mine")
        self.assertContains(response, "theirs")

    def test_everybody_else_sees_only_who_they_vouched_for(self):
        self.client.force_login(self.reviewer, backend=LOCAL_BACKEND)

        response = self.client.get(MANAGE)

        self.assertContains(response, "mine")
        self.assertNotContains(response, "theirs@example.org")

    def test_a_row_outside_your_branch_cannot_be_acted_on(self):
        """Leaving it off the listing is not the same as refusing the POST."""
        self.client.force_login(self.reviewer, backend=LOCAL_BACKEND)
        target = self.theirs.keycloak_identity

        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        with mock.patch.object(Provisioner, "__init__", init):
            response = self.client.post(
                MANAGE,
                {
                    "action": "send-email",
                    "identity": str(target.pk),
                    "confirm": "yes",
                },
                follow=True,
            )

        self.assertContains(response, "not in the realm")
        self.assertEqual(self.realm.emailed, [])

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        response = self.client.get(MANAGE)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_only_a_superuser_may_invite_an_existing_user(self):
        """It reaches the whole user list and grants a realm identity to
        somebody the inviter never brought in."""
        self.client.force_login(self.reviewer, backend=LOCAL_BACKEND)
        self.assertEqual(self.client.get(INVITE_EXISTING).status_code, 302)

        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)
        self.assertEqual(self.client.get(INVITE_EXISTING).status_code, 200)

    def test_the_invite_menu_offers_only_what_you_may_use(self):
        self.client.force_login(self.reviewer, backend=LOCAL_BACKEND)

        response = self.client.get(MANAGE)

        self.assertContains(response, INVITE_NEW)
        self.assertNotContains(response, INVITE_EXISTING)


@override_settings(**TRUST_SETTINGS)
class InviteNewTest(TestCase):
    """Creating an account for somebody who has none."""

    def setUp(self):
        self.realm = InvitingRealm()
        self.reviewer = contributor("rita", ["reviewer"])
        self.client.force_login(self.reviewer, backend=LOCAL_BACKEND)

    def invite(self, **form):
        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        data = {
            "username": "newbie",
            "email": "newbie@example.org",
            "first_name": "New",
            "last_name": "Bie",
            "role": "author",
        }
        data.update(form)
        with mock.patch.object(Provisioner, "__init__", init):
            return self.client.post(INVITE_NEW, data, follow=True)

    def test_it_creates_the_account_and_records_the_sponsor(self):
        """The regression test for the check constraint: the sponsor has to go
        in with the insert, not on the line after it."""
        self.invite()

        identity = KeycloakIdentity.objects.get(user__username="newbie")
        self.assertEqual(identity.sponsor, self.reviewer)
        self.assertEqual(identity.link_method, LinkMethod.INVITATION)
        self.assertFalse(identity.is_root)

    def test_the_account_is_passkey_only_like_every_other(self):
        self.invite()

        user = User.objects.get(username="newbie")
        self.assertFalse(user.has_usable_password())
        payload = self.realm.created[0]
        self.assertNotIn("credentials", payload)
        self.assertIn("webauthn-register-passwordless", payload["requiredActions"])

    def test_the_role_offered_is_the_role_granted(self):
        self.invite()

        self.assertEqual(self.realm.assigned, [("sub-newbie", ["author"])])

    def test_a_forged_role_is_refused_server_side(self):
        response = self.invite(role="admin")

        self.assertContains(
            response, "cannot invite somebody at that level", status_code=400
        )
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_a_username_already_taken_here_is_refused(self):
        User.objects.create_user("newbie", "other@example.org", "x")

        response = self.invite()

        self.assertContains(response, "taken here", status_code=400)
        self.assertEqual(self.realm.created, [])

    def test_an_address_already_known_here_is_refused(self):
        User.objects.create_user("other", "newbie@example.org", "x")

        response = self.invite()

        self.assertContains(response, "already an account here", status_code=400)

    def test_an_address_already_in_the_realm_is_refused(self):
        """It belongs to somebody, and enrolling onto it would send them a
        setup link they never asked for."""
        self.realm.existing_emails = {"newbie@example.org"}

        response = self.invite()

        self.assertContains(response, "already has a QGIS account", status_code=400)
        self.assertEqual(self.realm.created, [])

    def test_a_username_already_in_the_realm_is_refused(self):
        self.realm.existing = {"newbie"}

        response = self.invite()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.realm.created, [])

    @override_settings(SSO_TIER_QUOTAS={0: None, 1: 25, 2: 1, 3: 10, 4: 3})
    def test_somebody_at_quota_is_refused(self):
        contributor("already", ["author"], sponsor=self.reviewer)

        response = self.invite()

        self.assertContains(response, "no invitations left", status_code=400)
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_a_realm_failure_leaves_nothing_behind(self):
        self.realm.fail_on = {"newbie"}

        response = self.invite()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_inviting_is_audited(self):
        self.invite()

        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.INVITED, user__username="newbie"
            ).exists()
        )

    # -- the link it hands back -------------------------------------------

    def test_the_setup_link_is_shown_once_on_the_list(self):
        response = self.invite()

        self.assertContains(response, "login-actions/token?key=sub-newbie")

        again = self.client.get(MANAGE)
        self.assertNotContains(again, "login-actions/token")

    def test_nothing_is_emailed_by_inviting(self):
        """Sending is a button on the list, so it stays a deliberate act."""
        self.invite()

        self.assertEqual(self.realm.emailed, [])
