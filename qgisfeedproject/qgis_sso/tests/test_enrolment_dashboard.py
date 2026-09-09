# coding=utf-8
"""The two enrolment pages under ``/sso/manage/``.

They answer one question the admin changelists could not: where has each account
got to. The list is about accounts that exist in the realm and acts on one row
at a time; the create page is about accounts that do not, and is the only place
a selection is made. Most of what follows pins those two boundaries down, and
the rest pins the guards.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import enrolment
from ..models import KeycloakIdentity, SsoAuditEvent
from ..provisioning import Provisioner
from .base import FakeRealm

PAGE = reverse("qgis_sso:enrolment")
CREATE = reverse("qgis_sso:invite_existing")

# force_login picks the first configured backend, which is the OIDC one, and
# SessionRefresh would then bounce every GET to Keycloak to renew a token these
# fixtures never had.
LOCAL_BACKEND = "django.contrib.auth.backends.ModelBackend"

PAGE_SETTINGS = {
    "OIDC_RP_CLIENT_ID": "feed-qgis-org",
    "SSO_SETUP_REDIRECT_URI": "https://feed.example.org/oidc/authenticate/",
    "SSO_MAGIC_LINK_URL": "https://auth.example.org/realms/qgis/magic-link",
    "LOCAL_LOGIN_ENABLED": True,
    "AUTHENTICATION_BACKENDS": [
        "qgis_sso.auth.QGISOIDCAuthenticationBackend",
        "django.contrib.auth.backends.ModelBackend",
    ],
}


def link(user, **fields):
    """Bind a user to a realm subject, with whatever bookkeeping is asked for."""
    return KeycloakIdentity.objects.create(
        user=user,
        sub=f"sub-{user.username}",
        issuer="https://auth.example.org/realms/qgis",
        link_method="pre-sso-migration",
        **fields,
    )


class SuperuserPage(TestCase):
    """Signed in as somebody allowed to use these pages."""

    def setUp(self):
        self.realm = FakeRealm()
        self.superuser = User.objects.create_superuser(
            "root", "root@example.org", "x", last_login=timezone.now()
        )
        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)

    def post(self, url, data, follow=True):
        """Post with the realm stubbed out."""
        real_init = Provisioner.__init__

        def init(instance, client=None):
            real_init(instance, client=self.realm)

        with mock.patch.object(Provisioner, "__init__", init):
            return self.client.post(url, data, follow=follow)


class EnrolmentStateTest(TestCase):
    """One account, one state, read entirely from the local database."""

    def setUp(self):
        self.user = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )

    def state(self):
        return enrolment.rows([self.user])[0].state

    def test_an_account_with_no_identity_has_no_realm_account(self):
        self.assertEqual(self.state(), enrolment.NOT_PROVISIONED)

    def test_a_flagged_account_needs_a_decision(self):
        self.user.last_login = None
        self.user.save(update_fields=["last_login"])

        row = enrolment.rows([self.user])[0]

        self.assertEqual(row.state, enrolment.BLOCKED)
        self.assertIn("never-logged-in", row.flags)

    def test_a_created_account_is_not_yet_invited(self):
        link(self.user)

        self.assertEqual(self.state(), enrolment.PROVISIONED)

    def test_an_emailed_account_has_had_its_link_sent(self):
        link(self.user, setup_email_sent_at=timezone.now())

        self.assertEqual(self.state(), enrolment.LINK_SENT)

    def test_a_handed_over_link_counts_the_same_as_an_emailed_one(self):
        """Otherwise somebody invited on a call still reads as untouched, and
        gets invited a second time by whoever looks next."""
        link(self.user, setup_link_issued_at=timezone.now())

        self.assertEqual(self.state(), enrolment.LINK_SENT)

    def test_a_signed_in_account_is_active(self):
        link(self.user, first_sso_login_at=timezone.now())

        self.assertEqual(self.state(), enrolment.ACTIVE)

    def test_retiring_a_local_password_is_not_a_state(self):
        """It only ever happens to a grandfathered account and says nothing
        about how far that account has got, so it is a marker on the row."""
        link(
            self.user,
            first_sso_login_at=timezone.now(),
            local_password_disabled_at=timezone.now(),
        )

        self.assertEqual(self.state(), enrolment.ACTIVE)

    def test_building_rows_does_not_query_per_account(self):
        """The page is a list; an N+1 here is felt at the first migration wave.

        Two queries whatever the number of accounts: the identities, and who
        holds the publish permission. That second one is why ``proposed_roles``
        takes ``can_publish`` - ``has_perm`` cannot be prefetched and would be
        two more queries per row.
        """
        for index in range(10):
            user = User.objects.create_user(
                f"user{index}", f"user{index}@example.org", "x"
            )
            link(user)

        users = list(User.objects.all().prefetch_related("groups"))
        with self.assertNumQueries(2):
            enrolment.rows(users, flags={})


@override_settings(**PAGE_SETTINGS)
class AccessTest(TestCase):
    """Who may see these pages at all."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            "root", "root@example.org", "x", last_login=timezone.now()
        )

    def test_a_superuser_sees_both(self):
        self.client.force_login(self.superuser, backend=LOCAL_BACKEND)

        self.assertEqual(self.client.get(PAGE).status_code, 200)
        self.assertEqual(self.client.get(CREATE).status_code, 200)

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        """Asserted on the redirect, not on request.user.

        ``QgisFeedUserVisitMiddleware`` substitutes a shared ``qgis_user``
        account on anonymous requests, so being authenticated proves nothing
        here.
        """
        response = self.client.get(PAGE)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_the_list_is_open_but_inviting_an_existing_user_is_not(self):
        """The list scopes what each person sees rather than turning them away.
        Grafting an existing account reaches the whole user list, so that stays
        superuser-only."""
        staff = User.objects.create_user(
            "bob", "bob@example.org", "x", is_staff=True, is_superuser=False
        )
        self.client.force_login(staff, backend=LOCAL_BACKEND)

        self.assertEqual(self.client.get(PAGE).status_code, 200)
        self.assertEqual(self.client.get(CREATE).status_code, 302)


@override_settings(**PAGE_SETTINGS)
class ListingTest(SuperuserPage):
    """What the enrolment list shows, and what belongs on the other page."""

    def test_only_accounts_that_exist_in_the_realm_are_listed(self):
        """The list is about progress. An account with nobody behind it in the
        realm has not started, and belongs on the create page."""
        linked = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )
        link(linked)
        User.objects.create_user(
            "bob", "bob@example.org", "x", last_login=timezone.now()
        )

        response = self.client.get(PAGE)

        self.assertContains(response, "alice")
        self.assertNotContains(response, "bob@example.org")

    def test_the_list_paginates_at_ten(self):
        for index in range(12):
            user = User.objects.create_user(
                f"user{index}",
                f"user{index}@example.org",
                "x",
                last_login=timezone.now(),
            )
            link(user)

        page = self.client.get(PAGE).context["page"]

        self.assertEqual(len(page.object_list), 10)
        self.assertTrue(page.has_next())
        self.assertContains(self.client.get(PAGE), "records found")

    def test_the_create_page_is_linked(self):
        self.assertContains(self.client.get(PAGE), CREATE)


@override_settings(**PAGE_SETTINGS)
class CandidateListingTest(SuperuserPage):
    """What the create page offers, and what it refuses to."""

    def test_an_account_that_already_has_one_is_not_a_candidate(self):
        alice = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )
        link(alice)

        self.assertNotContains(self.client.get(CREATE), "alice@example.org")

    def test_an_account_with_no_address_is_not_a_candidate(self):
        """No invitation could reach it. It belongs in the admin user list."""
        User.objects.create_user("nomail", "", "x", last_login=timezone.now())

        response = self.client.get(CREATE)

        self.assertNotContains(response, "nomail")
        self.assertContains(response, "not shown")

    def test_a_deactivated_account_is_not_a_candidate(self):
        User.objects.create_user("closed", "closed@example.org", "x", is_active=False)

        self.assertNotContains(self.client.get(CREATE), "closed")

    def test_dormant_and_never_used_accounts_are_still_candidates(self):
        """Neither is an impossibility: both can be enrolled if somebody decides
        to. Only the flags that make enrolment pointless are hidden."""
        User.objects.create_user("dave", "dave@example.org", "x")
        User.objects.create_user(
            "erin",
            "erin@example.org",
            "x",
            last_login=timezone.now() - timedelta(days=800),
        )

        response = self.client.get(CREATE)

        self.assertContains(response, "dave")
        self.assertContains(response, "never-logged-in")
        self.assertContains(response, "erin")
        self.assertContains(response, "dormant")

    def test_the_candidate_list_is_not_paginated(self):
        """A selection cannot be lost by paging if there is no paging."""
        for index in range(30):
            User.objects.create_user(
                f"user{index}",
                f"user{index}@example.org",
                "x",
                last_login=timezone.now(),
            )

        response = self.client.get(CREATE)

        self.assertNotIn("page", response.context)
        self.assertContains(response, "user29")


@override_settings(**PAGE_SETTINGS)
class CreateAccountsTest(SuperuserPage):
    """Creating realm accounts from a selection."""

    def setUp(self):
        super().setUp()
        self.target = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )

    def test_a_first_post_only_previews(self):
        response = self.post(CREATE, {"selected": [str(self.target.pk)]})

        self.assertContains(response, "Will be created")
        self.assertEqual(self.realm.created, [])
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_a_confirmed_post_creates_the_account_and_emails_nobody(self):
        self.post(CREATE, {"selected": [str(self.target.pk)], "confirm": "yes"})

        self.assertEqual([u["username"] for u in self.realm.created], ["alice"])
        self.assertEqual(self.realm.emailed, [])
        self.assertTrue(KeycloakIdentity.objects.filter(user=self.target).exists())

    def test_a_held_back_account_is_named_with_its_reason(self):
        dormant = User.objects.create_user("dave", "dave@example.org", "x")

        response = self.post(CREATE, {"selected": [str(dormant.pk)], "confirm": "yes"})

        self.assertContains(response, "never-logged-in")
        self.assertEqual(self.realm.created, [])

    def test_a_completed_run_redirects_so_a_refresh_cannot_replay_it(self):
        response = self.post(
            CREATE,
            {"selected": [str(self.target.pk)], "confirm": "yes"},
            follow=False,
        )

        self.assertRedirects(response, CREATE)

    @override_settings(SSO_ADMIN_ACTION_MAX_USERS=1)
    def test_an_oversized_selection_is_refused_before_any_call(self):
        other = User.objects.create_user(
            "carol", "carol@example.org", "x", last_login=timezone.now()
        )

        response = self.post(
            CREATE,
            {
                "selected": [str(self.target.pk), str(other.pk)],
                "confirm": "yes",
            },
        )

        self.assertContains(response, "smaller waves")
        self.assertEqual(self.realm.created, [])

    def test_an_account_the_page_excludes_is_refused_when_posted_directly(self):
        """Leaving it off the listing is decoration unless the view refuses it."""
        closed = User.objects.create_user(
            "closed", "closed@example.org", "x", is_active=False
        )

        response = self.post(CREATE, {"selected": [str(closed.pk)], "confirm": "yes"})

        self.assertContains(response, "not candidates")
        self.assertEqual(self.realm.created, [])
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_an_account_that_already_has_one_is_refused_when_posted_directly(self):
        link(self.target)

        response = self.post(
            CREATE, {"selected": [str(self.target.pk)], "confirm": "yes"}
        )

        self.assertContains(response, "not candidates")
        self.assertEqual(self.realm.created, [])

    def test_a_selection_that_is_not_a_number_is_ignored(self):
        response = self.post(CREATE, {"selected": ["' OR 1=1 --"]})

        self.assertContains(response, "Nothing was selected")


@override_settings(**PAGE_SETTINGS)
class SendEmailTest(SuperuserPage):
    """Inviting one person from their row."""

    def setUp(self):
        super().setUp()
        self.target = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )
        self.identity = link(self.target)

    def test_it_confirms_before_emailing_anybody(self):
        """It reaches a real contributor and cannot be unsent."""
        response = self.post(
            PAGE, {"action": "send-email", "identity": str(self.identity.pk)}
        )

        self.assertContains(response, "cannot be unsent")
        self.assertEqual(self.realm.emailed, [])

    def test_a_confirmed_post_sends_and_records_it(self):
        self.post(
            PAGE,
            {
                "action": "send-email",
                "identity": str(self.identity.pk),
                "confirm": "yes",
            },
        )

        self.identity.refresh_from_db()
        self.assertEqual(self.realm.emailed, ["sub-alice"])
        self.assertEqual(self.identity.setup_email_send_count, 1)

    def test_an_identity_that_does_not_exist_is_refused(self):
        """The row carries an identity id, and a hand-made POST carries
        whatever it likes; it is resolved rather than trusted."""
        response = self.post(
            PAGE,
            {
                "action": "send-email",
                "identity": str(self.identity.pk + 999),
                "confirm": "yes",
            },
        )

        self.assertContains(response, "not in the realm")
        self.assertEqual(self.realm.emailed, [])


@override_settings(**PAGE_SETTINGS)
class IssuedLinkTest(SuperuserPage):
    """Handing a link over instead of emailing it.

    The link is a bearer credential: whoever holds it signs in as that person.
    So it is shown once, on one response, and exists nowhere else.
    """

    def setUp(self):
        super().setUp()
        self.target = User.objects.create_user(
            "alice", "alice@example.org", "x", last_login=timezone.now()
        )
        self.identity = link(self.target)

    def issue(self, follow=True):
        return self.post(
            PAGE,
            {"action": "issue-link", "identity": str(self.identity.pk)},
            follow=follow,
        )

    def test_the_link_comes_back_in_the_page_with_a_copy_button(self):
        response = self.issue()

        self.assertEqual(self.realm.linked, ["alice"])
        self.assertContains(response, "login-actions/token?key=sub-alice")
        self.assertContains(response, 'id="copy-link"')

    def test_it_is_not_confirmed_first(self):
        """Unlike the email, which cannot be unsent, nothing leaves the building
        until an administrator passes the link on."""
        response = self.issue()

        self.assertNotContains(response, "cannot be unsent")
        self.assertEqual(self.realm.linked, ["alice"])

    def test_the_reply_is_not_cached(self):
        response = self.issue(follow=False)

        self.assertEqual(response["Cache-Control"], "no-store")

    def test_the_link_is_never_stored(self):
        self.issue()

        self.identity.refresh_from_db()
        self.assertIsNotNone(self.identity.setup_link_issued_at)
        self.assertEqual(self.identity.setup_link_issue_count, 1)
        stored = " ".join(str(value) for value in self.identity.__dict__.values())
        self.assertNotIn("login-actions", stored)

    def test_the_audit_records_the_act_and_not_the_token(self):
        self.issue()

        event = SsoAuditEvent.objects.get(action=SsoAuditEvent.Action.SETUP_LINK_ISSUED)
        self.assertEqual(event.sub, "sub-alice")
        self.assertNotIn("login-actions", str(event.detail))

    def test_reloading_the_page_does_not_show_it_again(self):
        self.issue()

        response = self.client.get(PAGE)

        self.assertNotContains(response, "login-actions/token")

    def test_a_link_for_a_different_subject_is_refused(self):
        """The endpoint is keyed on the username; everything else here on sub.

        If a username has come to point at somebody else in the realm, handing
        the link over would sign that other person into this account.
        """
        self.realm.answer_with_subject = "sub-somebody-else"

        response = self.issue()

        self.assertNotContains(response, "login-actions/token")
        self.identity.refresh_from_db()
        self.assertIsNone(self.identity.setup_link_issued_at)
        self.assertFalse(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.SETUP_LINK_ISSUED
            ).exists()
        )

    def test_an_account_that_has_signed_in_is_refused_a_link(self):
        """Their required actions are spent, so the link would authenticate
        with no passkey and open a session across the whole realm - hub and
        plugins included. That is impersonation, not an invitation."""
        self.identity.first_sso_login_at = timezone.now()
        self.identity.save(update_fields=["first_sso_login_at"])

        response = self.issue()

        self.assertEqual(self.realm.linked, [])
        self.assertNotContains(response, "login-actions/token")
        self.assertContains(response, "already signed in")

    def test_the_button_is_not_offered_once_they_have_signed_in(self):
        self.identity.first_sso_login_at = timezone.now()
        self.identity.save(update_fields=["first_sso_login_at"])

        response = self.client.get(PAGE)

        self.assertNotContains(response, "Get the enrolment link")
        self.assertContains(response, "Email the account-setup link")
