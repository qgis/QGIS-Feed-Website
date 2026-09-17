# coding=utf-8
"""Moving an account to a new sponsor (US-5.4).

The way back for somebody a cascade suspended. Without it a revocation older
than the grace window leaves everybody below it suspended for good: the row
offers nothing, ``restore`` refuses anything not directly revoked, the admin is
read only, and they cannot be invited again because the account already exists.

    root ─ maria ─ rita ─ ali ─ nils
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import revocation
from ..models import KeycloakIdentity, SsoAuditEvent, TrustState
from .test_invitations import InvitingRealm
from .test_revocation import REVOKE_SETTINGS, person


@override_settings(**REVOKE_SETTINGS)
class ReparentTest(TestCase):
    def setUp(self):
        self.realm = InvitingRealm()
        self.root = person("root", ["admin"], is_root=True)
        self.root.is_superuser = True
        self.root.save(update_fields=["is_superuser"])

        self.maria = person("maria", ["web-maintainer"], sponsor=self.root)
        self.rita = person("rita", ["reviewer"], sponsor=self.maria)
        self.ali = person("ali", ["author"], sponsor=self.rita)
        self.nils = person("nils", ["author"], sponsor=self.ali)

    def identity(self, user):
        return KeycloakIdentity.objects.get(user=user)

    def cascade(self):
        """Revoke rita, suspending ali and nils below her."""
        revocation.revoke(
            self.root, self.identity(self.rita), "left the project", client=self.realm
        )

    # -- the rescue --------------------------------------------------------

    def test_it_brings_back_a_suspended_account_and_the_branch_below_it(self):
        self.cascade()

        revocation.reparent(
            self.root, self.identity(self.ali), self.maria, "still with us"
        )

        self.assertEqual(self.identity(self.ali).trust_state, TrustState.ACTIVE)
        self.assertEqual(self.identity(self.nils).trust_state, TrustState.ACTIVE)
        self.assertEqual(self.identity(self.ali).sponsor, self.maria)

    def test_it_works_after_the_grace_window_has_closed(self):
        """The regression this exists for.

        ``restore`` is refused by then, so without this the branch is stuck.
        """
        self.cascade()
        stale = timezone.now() - timedelta(days=90)
        KeycloakIdentity.objects.filter(
            user__in=[self.rita, self.ali, self.nils]
        ).update(revoked_at=stale)

        revocation.reparent(
            self.root, self.identity(self.ali), self.maria, "long overdue"
        )

        self.assertEqual(self.identity(self.ali).trust_state, TrustState.ACTIVE)

    def test_the_person_who_was_revoked_stays_revoked(self):
        """Rescuing the branch is not a way to undo the revocation itself."""
        self.cascade()

        revocation.reparent(
            self.root, self.identity(self.ali), self.maria, "still with us"
        )

        self.assertEqual(self.identity(self.rita).trust_state, TrustState.REVOKED)

    def test_it_leaves_alone_anybody_suspended_by_a_different_revocation(self):
        other = person("otto", ["reviewer"], sponsor=self.maria)
        below = person("bea", ["author"], sponsor=other)
        revocation.revoke(
            self.root, self.identity(other), "separate matter", client=self.realm
        )
        self.cascade()

        revocation.reparent(
            self.root, self.identity(self.ali), self.maria, "still with us"
        )

        self.assertEqual(self.identity(below).trust_state, TrustState.SUSPENDED)

    def test_it_puts_the_permissions_back(self):
        """A rescued account that can see nothing reads as a failed rescue."""
        self.cascade()

        revocation.reparent(
            self.root, self.identity(self.ali), self.maria, "still with us"
        )

        self.assertTrue(self.ali.groups.exists())

    # -- what it refuses ---------------------------------------------------

    def test_it_refuses_a_sponsor_from_inside_the_subtree(self):
        """That would close the chain into a loop."""
        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(self.root, self.identity(self.ali), self.nils, "a loop")

    def test_it_refuses_an_account_sponsoring_itself(self):
        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(self.root, self.identity(self.ali), self.ali, "itself")

    def test_it_refuses_a_sponsor_who_could_not_have_invited_them(self):
        """No escalation: a sponsor must outrank the roles being moved."""
        junior = person("jo", ["author"], sponsor=self.maria)

        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(
                self.root, self.identity(self.rita), junior, "too junior"
            )

    def test_it_refuses_a_sponsor_who_is_not_active(self):
        """Read from the database, not from what the caller is holding.

        ``self.rita`` still carries the identity cached on it when the account
        was created, and that copy says active: the revocation updated a row,
        not this instance. Trusting it handed the branch to somebody who had
        just been revoked.
        """
        self.cascade()

        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(
                self.root, self.identity(self.nils), self.rita, "already revoked"
            )

    def test_it_refuses_an_actor_who_is_not_senior_enough(self):
        """US-5.4 is a web maintainer's job, not a reviewer's.

        The actor here is active and untouched by the cascade, so it is the
        tier that refuses this and not the suspension.
        """
        otto = person("otto", ["reviewer"], sponsor=self.maria)
        self.cascade()

        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(
                otto, self.identity(self.nils), self.maria, "not mine to do"
            )

    def test_it_refuses_without_a_reason(self):
        self.cascade()

        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(self.root, self.identity(self.ali), self.maria, "  ")

    def test_a_refused_move_changes_nothing(self):
        self.cascade()

        with self.assertRaises(revocation.RevocationError):
            revocation.reparent(self.root, self.identity(self.ali), self.nils, "loop")

        self.assertEqual(self.identity(self.ali).sponsor, self.rita)
        self.assertEqual(self.identity(self.ali).trust_state, TrustState.SUSPENDED)

    # -- the record --------------------------------------------------------

    def test_it_is_audited_with_the_sponsor_before_and_after(self):
        self.cascade()

        revocation.reparent(
            self.root, self.identity(self.ali), self.maria, "still with us"
        )

        event = SsoAuditEvent.objects.filter(
            action=SsoAuditEvent.Action.REPARENTED
        ).latest("created_at")
        self.assertEqual(event.detail["was"], "rita")
        self.assertEqual(event.detail["now"], "maria")
        self.assertEqual(event.detail["reason"], "still with us")
        self.assertEqual(event.detail["by"], "root")

    # -- the picker --------------------------------------------------------

    def test_the_picker_excludes_the_account_and_everything_under_it(self):
        offered = {
            candidate.user_id
            for candidate in revocation.eligible_sponsors(self.identity(self.ali))
        }

        self.assertNotIn(self.ali.pk, offered)
        self.assertNotIn(self.nils.pk, offered)

    def test_the_picker_excludes_anybody_too_junior(self):
        junior = person("jo", ["author"], sponsor=self.maria)

        offered = {
            candidate.user_id
            for candidate in revocation.eligible_sponsors(self.identity(self.rita))
        }

        self.assertNotIn(junior.pk, offered)
        self.assertIn(self.maria.pk, offered)


@override_settings(**REVOKE_SETTINGS)
class ReparentPageTest(TestCase):
    """The page, and that it cannot be talked past the picker."""

    def setUp(self):
        self.realm = InvitingRealm()
        self.root = person("root", ["admin"], is_root=True)
        self.root.is_superuser = True
        self.root.save(update_fields=["is_superuser"])
        self.maria = person("maria", ["web-maintainer"], sponsor=self.root)
        self.rita = person("rita", ["reviewer"], sponsor=self.maria)
        self.ali = person("ali", ["author"], sponsor=self.rita)

        revocation.revoke(
            self.root,
            KeycloakIdentity.objects.get(user=self.rita),
            "left",
            client=self.realm,
        )
        self.client.force_login(
            self.root, backend="django.contrib.auth.backends.ModelBackend"
        )

    def url(self):
        identity = KeycloakIdentity.objects.get(user=self.ali)
        return reverse("qgis_sso:reparent", args=[identity.pk])

    def test_it_shows_the_page(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_a_posted_sponsor_outside_the_picker_is_refused(self):
        """The list is the check, not a suggestion."""
        response = self.client.post(
            self.url(), {"sponsor": str(self.ali.pk), "reason": "a loop"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KeycloakIdentity.objects.get(user=self.ali).sponsor, self.rita)

    def test_it_moves_the_account(self):
        response = self.client.post(
            self.url(), {"sponsor": str(self.maria.pk), "reason": "still with us"}
        )

        self.assertRedirects(response, reverse("qgis_sso:enrolment"))
        identity = KeycloakIdentity.objects.get(user=self.ali)
        self.assertEqual(identity.sponsor, self.maria)
        self.assertEqual(identity.trust_state, TrustState.ACTIVE)
