# coding=utf-8
"""Offering to sponsor somebody a cascade suspended, and answering the offer.

The peer's way out of a cascade. A maintainer moves people between sponsors
outright; a colleague of the same standing can only offer, and the suspended
person decides.

    root ─ maria ─ rita ─ ali ─ nils
                      └─ dana

``cascade`` revokes maria, so rita, ali, nils and dana are all suspended and
every rank below tier 1 is represented among them.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .. import revocation, sponsorship
from ..models import (
    KeycloakIdentity,
    OfferState,
    SponsorshipOffer,
    SsoAuditEvent,
    TrustState,
)
from .test_invitations import InvitingRealm
from .test_revocation import REVOKE_SETTINGS, person


@override_settings(**REVOKE_SETTINGS)
class SponsorshipTestCase(TestCase):
    """The graph, and the cascade that suspends most of it."""

    def setUp(self):
        self.realm = InvitingRealm()
        self.root = person("root", ["admin"], is_root=True)
        self.root.is_superuser = True
        self.root.save(update_fields=["is_superuser"])

        self.maria = person("maria", ["web-maintainer"], sponsor=self.root)
        self.rita = person("rita", ["reviewer"], sponsor=self.maria)
        self.ali = person("ali", ["author"], sponsor=self.rita)
        self.nils = person("nils", ["author"], sponsor=self.ali)
        self.dana = person("dana", ["reviewer"], sponsor=self.rita)

        # Peers outside the branch the cascade reaches, so their own standing
        # is the only thing that can refuse them.
        self.otto = person("otto", ["author"], sponsor=self.root)
        self.vera = person("vera", ["reviewer"], sponsor=self.root)

    def identity(self, user):
        return KeycloakIdentity.objects.get(user=user)

    def fresh(self, user):
        """The user again, without an identity cached from before a change."""
        return User.objects.get(pk=user.pk)

    def cascade(self):
        """Revoke maria, suspending her whole branch."""
        revocation.revoke(
            self.root, self.identity(self.maria), "left the project", client=self.realm
        )

    def free(self, user):
        """Make one account active again, leaving its branch suspended.

        Set by hand rather than through ``reparent``, which also moves the
        sponsor. These two tests are about an account whose sponsor chain is
        still suspended, which is the state the cycle and the already-sponsoring
        rules exist for.
        """
        identity = self.identity(user)
        identity.trust_state = TrustState.ACTIVE
        identity.save(update_fields=["trust_state"])


class WhoMayOfferTest(SponsorshipTestCase):
    def test_a_peer_of_the_same_tier_may_offer(self):
        self.cascade()

        self.assertTrue(
            revocation.may_sponsor(self.fresh(self.otto), self.identity(self.ali))
        )

    def test_somebody_more_senior_may_offer(self):
        self.cascade()

        self.assertTrue(
            revocation.may_sponsor(self.fresh(self.vera), self.identity(self.ali))
        )

    def test_somebody_too_junior_may_not_offer(self):
        self.cascade()

        self.assertFalse(
            revocation.may_sponsor(self.fresh(self.otto), self.identity(self.rita))
        )

    def test_a_suspended_account_may_not_offer(self):
        self.cascade()

        self.assertFalse(
            revocation.may_sponsor(self.fresh(self.dana), self.identity(self.nils))
        )

    def test_an_account_revoked_in_its_own_right_cannot_be_taken_on(self):
        """Only a cascade is undone this way. A decision about somebody stands."""
        self.cascade()

        self.assertFalse(
            revocation.may_sponsor(self.fresh(self.vera), self.identity(self.maria))
        )

    def test_nobody_sponsors_their_own_account(self):
        self.cascade()

        self.assertFalse(
            revocation.may_sponsor(self.fresh(self.ali), self.identity(self.ali))
        )

    def test_the_current_sponsor_is_not_offered_the_job_again(self):
        self.cascade()
        self.free(self.rita)

        refused = revocation.sponsor_refusal(
            self.fresh(self.rita), self.identity(self.ali)
        )

        self.assertIn("already sponsor", str(refused))

    def test_somebody_above_them_in_the_tree_cannot_take_them_on(self):
        """A sponsor from below would close the graph into a loop."""
        self.cascade()
        self.free(self.dana)

        refused = revocation.sponsor_refusal(
            self.fresh(self.dana), self.identity(self.rita)
        )

        self.assertIn("above yours", str(refused))

    def test_a_sponsor_with_no_free_place_is_refused_for_somebody_who_never_arrived(
        self,
    ):
        late = person("late", ["author"], sponsor=self.rita, arrived=False)
        for spare in range(3):
            person(f"waiting{spare}", ["author"], sponsor=self.otto, arrived=False)
        self.cascade()

        self.assertFalse(
            revocation.may_sponsor(self.fresh(self.otto), self.identity(late))
        )
        # The same sponsor, and somebody who has already signed in, costs
        # nothing: the quota counts places still being held.
        self.assertTrue(
            revocation.may_sponsor(self.fresh(self.otto), self.identity(self.ali))
        )


class MakingAnOfferTest(SponsorshipTestCase):
    def test_it_records_the_offer_and_audits_it(self):
        self.cascade()

        made = sponsorship.offer(
            self.fresh(self.otto), self.identity(self.ali), " we work together "
        )

        self.assertEqual(made.state, OfferState.PENDING)
        self.assertEqual(made.reason, "we work together")
        event = SsoAuditEvent.objects.filter(
            action=SsoAuditEvent.Action.SPONSORSHIP_OFFERED
        ).latest("created_at")
        self.assertEqual(event.username, "ali")
        self.assertEqual(event.detail["by"], "otto")

    def test_it_refuses_without_a_reason(self):
        self.cascade()

        with self.assertRaises(revocation.RevocationError):
            sponsorship.offer(self.fresh(self.otto), self.identity(self.ali), "   ")

    def test_it_refuses_a_second_open_offer_from_the_same_person(self):
        self.cascade()
        sponsorship.offer(self.fresh(self.otto), self.identity(self.ali), "once")

        with self.assertRaises(revocation.RevocationError):
            sponsorship.offer(self.fresh(self.otto), self.identity(self.ali), "twice")

    def test_two_people_may_offer_at_the_same_time(self):
        self.cascade()

        sponsorship.offer(self.fresh(self.otto), self.identity(self.ali), "me")
        sponsorship.offer(self.fresh(self.vera), self.identity(self.ali), "or me")

        self.assertEqual(sponsorship.pending_for(self.identity(self.ali)).count(), 2)

    def test_it_refuses_somebody_too_junior(self):
        self.cascade()

        with self.assertRaises(revocation.RevocationError):
            sponsorship.offer(self.fresh(self.otto), self.identity(self.rita), "mine")


class AnsweringAnOfferTest(SponsorshipTestCase):
    def offer_to(self, user, sponsor, reason="we work together"):
        return sponsorship.offer(self.fresh(sponsor), self.identity(user), reason)

    def test_accepting_moves_the_sponsor_and_lifts_the_cascade(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        sponsorship.accept(self.fresh(self.ali), made)

        self.assertEqual(self.identity(self.ali).sponsor, self.otto)
        self.assertEqual(self.identity(self.ali).trust_state, TrustState.ACTIVE)
        # The branch below comes back with them, which is the point of a rescue.
        self.assertEqual(self.identity(self.nils).trust_state, TrustState.ACTIVE)
        # Not the branch beside them.
        self.assertEqual(self.identity(self.dana).trust_state, TrustState.SUSPENDED)

    def test_accepting_writes_both_records(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        sponsorship.accept(self.fresh(self.ali), made)

        made.refresh_from_db()
        self.assertEqual(made.state, OfferState.ACCEPTED)
        self.assertIsNotNone(made.responded_at)
        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.SPONSORSHIP_ACCEPTED, username="ali"
            ).exists()
        )
        self.assertTrue(
            SsoAuditEvent.objects.filter(
                action=SsoAuditEvent.Action.REPARENTED, username="ali"
            ).exists()
        )

    def test_accepting_one_offer_withdraws_the_others(self):
        self.cascade()
        mine = self.offer_to(self.ali, self.otto)
        theirs = self.offer_to(self.ali, self.vera)

        sponsorship.accept(self.fresh(self.ali), mine)

        theirs.refresh_from_db()
        self.assertEqual(theirs.state, OfferState.WITHDRAWN)
        self.assertIsNotNone(theirs.responded_at)

    def test_only_the_person_it_was_made_to_may_accept(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        with self.assertRaises(revocation.RevocationError):
            sponsorship.accept(self.fresh(self.nils), made)

        self.assertEqual(self.identity(self.ali).trust_state, TrustState.SUSPENDED)

    def test_an_offer_from_somebody_since_revoked_is_refused(self):
        """Standing is re-read at acceptance, not trusted from when it was made."""
        self.cascade()
        made = self.offer_to(self.ali, self.otto)
        revocation.revoke(
            self.root, self.identity(self.otto), "left too", client=self.realm
        )

        with self.assertRaises(revocation.RevocationError):
            sponsorship.accept(self.fresh(self.ali), made)

        self.assertEqual(self.identity(self.ali).sponsor, self.rita)
        self.assertEqual(self.identity(self.ali).trust_state, TrustState.SUSPENDED)

    def test_a_refused_acceptance_leaves_the_offer_waiting(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)
        revocation.revoke(
            self.root, self.identity(self.otto), "left too", client=self.realm
        )

        with self.assertRaises(revocation.RevocationError):
            sponsorship.accept(self.fresh(self.ali), made)

        made.refresh_from_db()
        self.assertEqual(made.state, OfferState.PENDING)

    def test_an_answered_offer_cannot_be_answered_again(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)
        sponsorship.accept(self.fresh(self.ali), made)

        with self.assertRaises(revocation.RevocationError):
            sponsorship.accept(self.fresh(self.ali), made)

    def test_declining_changes_nothing_but_the_offer(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        sponsorship.decline(self.fresh(self.ali), made)

        made.refresh_from_db()
        self.assertEqual(made.state, OfferState.DECLINED)
        self.assertEqual(self.identity(self.ali).sponsor, self.rita)
        self.assertEqual(self.identity(self.ali).trust_state, TrustState.SUSPENDED)

    def test_nobody_declines_an_offer_made_to_somebody_else(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        with self.assertRaises(revocation.RevocationError):
            sponsorship.decline(self.fresh(self.nils), made)

    def test_the_sponsor_may_take_their_offer_back(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        sponsorship.withdraw(self.fresh(self.otto), made)

        made.refresh_from_db()
        self.assertEqual(made.state, OfferState.WITHDRAWN)

    def test_nobody_else_takes_an_offer_back(self):
        self.cascade()
        made = self.offer_to(self.ali, self.otto)

        with self.assertRaises(revocation.RevocationError):
            sponsorship.withdraw(self.fresh(self.vera), made)


class OfferPageTest(SponsorshipTestCase):
    """The page a would-be sponsor fills in."""

    def sign_in(self, user):
        self.client.force_login(
            user, backend="django.contrib.auth.backends.ModelBackend"
        )

    def url(self, user):
        return reverse("qgis_sso:sponsor_offer", args=[self.identity(user).pk])

    def test_a_peer_can_read_the_page(self):
        self.cascade()
        self.sign_in(self.otto)

        response = self.client.get(self.url(self.ali))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ali")

    def test_the_page_never_shows_the_address(self):
        self.cascade()
        self.sign_in(self.otto)

        response = self.client.get(self.url(self.ali))

        self.assertNotContains(response, self.ali.email)

    def test_somebody_too_junior_is_sent_back_with_a_reason(self):
        self.cascade()
        self.sign_in(self.otto)

        response = self.client.get(self.url(self.rita))

        self.assertRedirects(response, reverse("qgis_sso:enrolment"))

    def test_posting_without_a_reason_asks_again(self):
        self.cascade()
        self.sign_in(self.otto)

        response = self.client.post(self.url(self.ali), {"reason": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(SponsorshipOffer.objects.exists())

    def test_posting_a_reason_makes_the_offer(self):
        self.cascade()
        self.sign_in(self.otto)

        response = self.client.post(self.url(self.ali), {"reason": "we work together"})

        self.assertRedirects(response, reverse("qgis_sso:enrolment"))
        self.assertEqual(SponsorshipOffer.objects.count(), 1)

    def test_signing_in_is_required(self):
        self.cascade()

        response = self.client.get(self.url(self.ali))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class AnswerFromTheProfilePageTest(SponsorshipTestCase):
    """A suspended account can act on this one page and nowhere else."""

    def sign_in(self, user):
        self.client.force_login(
            user, backend="django.contrib.auth.backends.ModelBackend"
        )

    def test_the_offer_is_on_the_page(self):
        self.cascade()
        sponsorship.offer(
            self.fresh(self.otto), self.identity(self.ali), "we work together"
        )
        self.sign_in(self.ali)

        response = self.client.get(reverse("qgis_sso:profile"))

        self.assertContains(response, "we work together")

    def test_accepting_from_the_page_moves_the_sponsor(self):
        self.cascade()
        made = sponsorship.offer(
            self.fresh(self.otto), self.identity(self.ali), "we work together"
        )
        self.sign_in(self.ali)

        self.client.post(
            reverse("qgis_sso:profile"),
            {"action": "accept-sponsorship", "offer": made.pk},
        )

        self.assertEqual(self.identity(self.ali).sponsor, self.otto)
        self.assertEqual(self.identity(self.ali).trust_state, TrustState.ACTIVE)

    def test_nobody_answers_an_offer_by_posting_its_id(self):
        self.cascade()
        made = sponsorship.offer(
            self.fresh(self.otto), self.identity(self.ali), "we work together"
        )
        self.sign_in(self.nils)

        self.client.post(
            reverse("qgis_sso:profile"),
            {"action": "accept-sponsorship", "offer": made.pk},
        )

        self.assertEqual(self.identity(self.ali).sponsor, self.rita)
        made.refresh_from_db()
        self.assertEqual(made.state, OfferState.PENDING)

    def test_the_sponsor_withdraws_from_their_own_page(self):
        self.cascade()
        made = sponsorship.offer(
            self.fresh(self.otto), self.identity(self.ali), "we work together"
        )
        self.sign_in(self.otto)

        self.client.post(
            reverse("qgis_sso:profile"),
            {"action": "withdraw-sponsorship", "offer": made.pk},
        )

        made.refresh_from_db()
        self.assertEqual(made.state, OfferState.WITHDRAWN)
