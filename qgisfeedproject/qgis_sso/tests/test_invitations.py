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

from .. import revocation, tiers
from ..auth import expected_issuer
from ..keycloak import KeycloakAdminClient, KeycloakError
from ..models import KeycloakIdentity, LinkMethod, SsoAuditEvent, TrustState
from ..provisioning import Provisioner
from .base import FakeRealm

MANAGE = reverse("qgis_sso:enrolment")
ISSUED_LINK = reverse("qgis_sso:issued_link")
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
    """The provisioning double, plus the address lookup inviting makes.

    ``existing_emails`` is the shorthand for "the realm knows this address and
    nothing more matters"; ``realm_users`` on the base class is for the tests
    that care what the account looks like.
    """

    def __init__(self, *args, existing_emails=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_emails = set(existing_emails)

    def find_user_by_email(self, email):
        if email in self.existing_emails:
            return {"id": "somebody"}
        return super().find_user_by_email(email)


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

    @override_settings(SSO_TIER_QUOTAS={0: None, 1: 25, 2: 1, 3: 10, 4: 3})
    def test_a_place_frees_up_when_the_account_goes(self):
        """The quota limits invitations open at once, not invitations ever.

        Cancelling an invitation deletes the account, so the place comes back
        with nothing left to mark. Without that the limit was a lifetime cap:
        a mistyped address held a place for good.
        """
        reviewer = contributor("rita", ["reviewer"])
        invitee = contributor("one", ["author"], sponsor=reviewer)
        self.assertEqual(tiers.remaining(reviewer), 0)

        invitee.keycloak_identity.delete()

        self.assertEqual(tiers.remaining(reviewer), 1)

    @override_settings(SSO_TIER_QUOTAS={0: None, 1: 25, 2: 1, 3: 10, 4: 3})
    def test_a_place_held_by_an_untrusted_account_does_not_count(self):
        """Nobody can ever use it, so charging the sponsor for it is a leak."""
        reviewer = contributor("rita", ["reviewer"])
        invitee = contributor("one", ["author"], sponsor=reviewer)
        identity = invitee.keycloak_identity
        identity.trust_state = TrustState.REVOKED
        identity.save(update_fields=["trust_state"])

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

        self.assertContains(response, "could not find that account")
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

    @override_settings(
        SSO_ISSUER="https://auth.example.org/realms/qgis",
        SSO_KEYCLOAK_SERVER_URL="http://keycloak.internal:8080",
    )
    def test_the_issuer_recorded_is_the_one_sign_in_asserts(self):
        """The regression test for an invitation that refuses itself.

        The address the admin API is reached on and the issuer inside a token
        are two different facts. Recording the first and checking the second
        turned every new account into one that could never sign in, and the
        only way back was an UPDATE by hand.
        """
        self.invite()

        identity = KeycloakIdentity.objects.get(user__username="newbie")
        self.assertEqual(identity.issuer, expected_issuer())
        self.assertEqual(identity.issuer, "https://auth.example.org/realms/qgis")

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

        self.assertContains(response, "waiting for somebody", status_code=400)
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

    def test_the_setup_link_is_shown_once_on_its_own_page(self):
        response = self.invite()

        self.assertEqual(response.request["PATH_INFO"], ISSUED_LINK)
        self.assertContains(response, "login-actions/token?key=sub-newbie")
        self.assertContains(response, "<svg")

        again = self.client.get(ISSUED_LINK)
        self.assertNotContains(again, "login-actions/token")

    def test_nothing_is_emailed_by_inviting(self):
        """Sending is a button on the list, so it stays a deliberate act."""
        self.invite()

        self.assertEqual(self.realm.emailed, [])


@override_settings(**TRUST_SETTINGS)
class WhoMayDoWhatTest(TestCase):
    """The three routes, and who each is for.

    Creating a realm account for somebody who already has an account here, and
    binding one to a realm account that already exists, both reach the whole
    user list. Inviting somebody new does not - it is bounded by the ladder and
    the quota, which is the point of having them.
    """

    def setUp(self):
        self.realm = InvitingRealm(
            realm_users=[
                {
                    "id": "sub-existing",
                    "username": "dave-qgis",
                    "email": "dave@example.org",
                    "emailVerified": True,
                }
            ]
        )
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
        # Somebody the realm already knows, so the link path is live.
        self.dave = User.objects.create_user("dave", "dave@example.org", "x")
        self.dave.last_login = timezone.now()
        self.dave.save(update_fields=["last_login"])

    def act_as(self, user):
        self.client.force_login(user, backend=LOCAL_BACKEND)

    def post_existing(self, **data):
        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        payload = {"selected": [str(self.dave.pk)], "confirm": "yes"}
        payload.update(data)
        with mock.patch.object(Provisioner, "__init__", init):
            return self.client.post(INVITE_EXISTING, payload, follow=True)

    def test_a_superuser_may_link_an_existing_realm_account(self):
        self.act_as(self.superuser)

        self.post_existing()

        identity = KeycloakIdentity.objects.get(user=self.dave)
        self.assertEqual(identity.sub, "sub-existing")
        self.assertEqual(self.realm.created, [])

    def test_a_reviewer_may_not_link_even_by_posting(self):
        """The menu hides it; the view has to refuse it as well."""
        self.act_as(self.reviewer)

        self.post_existing()

        self.assertFalse(KeycloakIdentity.objects.filter(user=self.dave).exists())

    def test_a_reviewer_may_not_create_for_an_existing_account_either(self):
        somebody = User.objects.create_user("erin", "erin@example.org", "x")
        somebody.last_login = timezone.now()
        somebody.save(update_fields=["last_login"])
        self.act_as(self.reviewer)

        self.post_existing(selected=[str(somebody.pk)])

        self.assertEqual(self.realm.created, [])
        self.assertFalse(KeycloakIdentity.objects.filter(user=somebody).exists())

    def test_a_reviewer_may_invite_somebody_new(self):
        """Bounded by the ladder and the quota rather than by being a
        superuser, which is what the web of trust is for."""
        self.act_as(self.reviewer)

        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        with mock.patch.object(Provisioner, "__init__", init):
            self.client.post(
                INVITE_NEW,
                {
                    "username": "newbie",
                    "email": "newbie@example.org",
                    "first_name": "New",
                    "last_name": "Bie",
                    "role": "author",
                },
                follow=True,
            )

        identity = KeycloakIdentity.objects.get(user__username="newbie")
        self.assertEqual(identity.sponsor, self.reviewer)

    def test_a_staff_user_with_no_realm_account_may_invite_nobody(self):
        """No identity means no tier, so no quota and nothing to offer."""
        staff = User.objects.create_user("bob", "bob@example.org", "x", is_staff=True)
        self.act_as(staff)

        self.assertEqual(self.client.get(INVITE_NEW).status_code, 302)
        self.assertEqual(self.client.get(INVITE_EXISTING).status_code, 302)


@override_settings(**TRUST_SETTINGS)
class CancellingAnInvitationTest(TestCase):
    """Undoing an invitation, and what each row is allowed to offer.

    Cancelling puts things back as they were before anybody pressed Invite: the
    realm account, the identity and the local user all go. The place comes back
    because the account it stood for is gone, and the same person can be invited
    again with the same name and address.
    """

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
        self.invitee = contributor("one", ["author"], sponsor=self.reviewer)
        self.client.force_login(self.reviewer, backend=LOCAL_BACKEND)

    def cancel(self, identity, confirm="yes"):
        data = {"action": "cancel-invitation", "identity": str(identity.pk)}
        if confirm:
            data["confirm"] = confirm
        with mock.patch(
            "qgis_sso.keycloak.KeycloakAdminClient", return_value=self.realm
        ):
            return self.client.post(MANAGE, data, follow=True)

    def test_it_asks_before_it_acts(self):
        response = self.cancel(self.invitee.keycloak_identity, confirm=None)

        self.assertContains(response, "Cancel this invitation?")
        self.assertEqual(self.realm.deleted, [])
        self.assertTrue(User.objects.filter(username="one").exists())

    def test_everything_the_invitation_made_is_removed(self):
        self.cancel(self.invitee.keycloak_identity)

        self.assertEqual(self.realm.deleted, ["sub-one"])
        self.assertFalse(KeycloakIdentity.objects.filter(sub="sub-one").exists())
        self.assertFalse(User.objects.filter(username="one").exists())

    @override_settings(SSO_TIER_QUOTAS={0: None, 1: 25, 2: 1, 3: 10, 4: 3})
    def test_the_place_comes_back(self):
        self.assertEqual(tiers.remaining(self.reviewer), 0)

        response = self.cancel(self.invitee.keycloak_identity)

        self.assertContains(response, "is cancelled and the account is gone")
        self.assertEqual(tiers.remaining(self.reviewer), 1)

    def test_the_same_person_can_be_invited_again(self):
        """Nothing is kept, so the name and the address are free."""
        self.cancel(self.invitee.keycloak_identity)

        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        with mock.patch.object(Provisioner, "__init__", init):
            self.client.post(
                INVITE_NEW,
                {
                    "username": "one",
                    "email": "one@example.org",
                    "first_name": "One",
                    "last_name": "Again",
                    "role": "author",
                },
                follow=True,
            )

        identity = KeycloakIdentity.objects.get(user__username="one")
        self.assertEqual(identity.sponsor, self.reviewer)

    def test_a_realm_that_refuses_removes_nothing(self):
        """The realm goes first, so a refusal leaves everything standing."""
        with mock.patch(
            "qgis_sso.keycloak.KeycloakAdminClient", side_effect=KeycloakError("no")
        ):
            response = self.client.post(
                MANAGE,
                {
                    "action": "cancel-invitation",
                    "identity": str(self.invitee.keycloak_identity.pk),
                    "confirm": "yes",
                },
                follow=True,
            )

        self.assertContains(response, "could not reach")
        self.assertTrue(KeycloakIdentity.objects.filter(sub="sub-one").exists())
        self.assertTrue(User.objects.filter(username="one").exists())

    def test_it_is_audited_and_the_record_outlives_the_account(self):
        self.cancel(self.invitee.keycloak_identity)

        event = SsoAuditEvent.objects.get(
            action=SsoAuditEvent.Action.INVITATION_CANCELLED
        )
        self.assertEqual(event.username, "one")
        self.assertIsNone(event.user)
        self.assertEqual(event.detail["cancelled_by"], "rita")
        self.assertEqual(event.detail["sponsor"], "rita")

    def test_an_account_that_did_not_arrive_on_an_invitation_is_refused(self):
        """A migrated account keeps its own records, whoever sponsors it."""
        migrated = contributor("mira", ["author"], sponsor=self.reviewer)
        identity = migrated.keycloak_identity
        identity.link_method = LinkMethod.PRE_SSO_MIGRATION
        identity.save(update_fields=["link_method"])

        response = self.cancel(identity)

        self.assertContains(response, "no invitation to cancel")
        self.assertEqual(self.realm.deleted, [])
        self.assertTrue(User.objects.filter(username="mira").exists())

    def test_there_is_nothing_to_cancel_once_they_have_signed_in(self):
        identity = self.invitee.keycloak_identity
        identity.first_sso_login_at = timezone.now()
        identity.save(update_fields=["first_sso_login_at"])

        response = self.cancel(identity)

        self.assertContains(response, "Withdraw trust from them instead")
        self.assertEqual(self.realm.deleted, [])
        self.assertTrue(User.objects.filter(username="one").exists())

    def test_nobody_cancels_an_invitation_outside_their_own_branch(self):
        theirs = contributor("theirs", ["author"], sponsor=self.superuser)

        response = self.cancel(theirs.keycloak_identity)

        self.assertContains(response, "could not find that account")
        self.assertEqual(self.realm.deleted, [])
        self.assertTrue(User.objects.filter(username="theirs").exists())

    def test_an_administrator_may_cancel_any_invitation(self):
        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)

        self.cancel(self.invitee.keycloak_identity)

        self.assertFalse(User.objects.filter(username="one").exists())

    # -- what each row offers ---------------------------------------------

    def test_a_pending_invitation_offers_setting_up_and_cancelling(self):
        response = self.client.get(MANAGE)

        self.assertContains(response, "send-email")
        self.assertContains(response, "issue-link")
        self.assertContains(response, "cancel-invitation")
        self.assertNotContains(response, "Withdraw trust from one")

    def test_an_account_that_signed_in_offers_withdrawing_trust_only(self):
        identity = self.invitee.keycloak_identity
        identity.first_sso_login_at = timezone.now()
        identity.save(update_fields=["first_sso_login_at"])

        response = self.client.get(MANAGE)

        self.assertContains(response, "Withdraw trust from one")
        self.assertNotContains(response, "send-email")
        self.assertNotContains(response, "issue-link")
        self.assertNotContains(response, "cancel-invitation")

    def test_nothing_is_set_up_for_somebody_who_has_signed_in(self):
        """Refused on the post too, not only hidden on the row."""
        identity = self.invitee.keycloak_identity
        identity.first_sso_login_at = timezone.now()
        identity.save(update_fields=["first_sso_login_at"])

        for action in ("send-email", "issue-link"):
            with mock.patch(
                "qgis_sso.keycloak.KeycloakAdminClient", return_value=self.realm
            ):
                response = self.client.post(
                    MANAGE,
                    {"action": action, "identity": str(identity.pk), "confirm": "yes"},
                    follow=True,
                )

            self.assertContains(response, "signed in already")
        self.assertEqual(self.realm.emailed, [])

    def test_trust_cannot_be_withdrawn_from_somebody_who_never_arrived(self):
        """Cancelling the invitation is what is meant, and the reason says so."""
        reason = revocation.refusal(self.reviewer, self.invitee.keycloak_identity)

        self.assertIn("Cancel their invitation instead", str(reason))


CLIENT_SETTINGS = {
    "SSO_KEYCLOAK_SERVER_URL": "https://auth.example.org",
    "SSO_KEYCLOAK_REALM": "qgis",
    "SSO_PROVISIONER_CLIENT_ID": "feed-qgis-org-provisioner",
    "SSO_PROVISIONER_CLIENT_SECRET": "not-a-real-secret",
}


@override_settings(**CLIENT_SETTINGS)
class DeletingARealmUserTest(TestCase):
    """The one realm call cancelling makes, and its one tolerance.

    Everything else this client does treats a 404 as a failure. Deleting has to
    treat it as the job already being done, or finishing a cancel that stopped
    halfway would be impossible.
    """

    def setUp(self):
        self.realm = KeycloakAdminClient()
        self.realm._token = "service-account-token"
        self.realm._token_expires_at = float("inf")

    def reply(self, status):
        answer = mock.Mock()
        answer.status_code = status
        answer.text = ""
        return mock.patch.object(self.realm._session, "request", return_value=answer)

    def test_it_asks_keycloak_to_delete_the_subject(self):
        with self.reply(204) as asked:
            self.realm.delete_user("sub-one")

        method, url = asked.call_args.args
        self.assertEqual(method, "DELETE")
        self.assertTrue(url.endswith("/users/sub-one"))

    def test_an_account_already_gone_is_not_an_error(self):
        with self.reply(404):
            self.assertIsNone(self.realm.delete_user("sub-one"))

    def test_any_other_refusal_still_raises(self):
        with self.reply(403):
            with self.assertRaises(KeycloakError):
                self.realm.delete_user("sub-one")
