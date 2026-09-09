# coding=utf-8
"""Withdrawing trust, and giving it back.

Exercised on a graph four levels deep, as US-8.3 asks, because the cascade is
the part that cannot be reasoned about from a two-node example.

    root ─ maria ─ rita ─ ali ─ nils

The rule that everything else rests on: a suspended account must stay
suspended across a sign-in. Role mirroring reconciles Django's groups from the
Keycloak token every time somebody authenticates, so anything that only removes
groups is undone within seconds.
"""

from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import SuspiciousOperation
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import revocation, tiers
from ..models import KeycloakIdentity, SsoAuditEvent, TrustState
from ..roles import mirror_roles
from .test_invitations import TRUST_SETTINGS, InvitingRealm

REVOKE_SETTINGS = dict(TRUST_SETTINGS, SSO_ON_REVOKE="", SSO_REVOCATION_GRACE_DAYS=7)


def person(username, roles, sponsor=None, is_root=False):
    user = User.objects.create_user(username, f"{username}@example.org", "x")
    KeycloakIdentity.objects.create(
        user=user,
        sub=f"sub-{username}",
        issuer="https://auth.example.org/realms/qgis",
        link_method="invitation" if sponsor else "pre-sso-migration",
        last_seen_roles=list(roles),
        sponsor=sponsor,
        is_root=is_root or sponsor is None,
    )
    return user


@override_settings(**REVOKE_SETTINGS)
class TrustChainTest(TestCase):
    """A chain deep enough for the cascade to mean something."""

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

    def revoke(self, actor, target, reason="no longer with the project"):
        return revocation.revoke(
            actor, self.identity(target), reason, client=self.realm
        )

    # -- who may act on whom ----------------------------------------------

    def test_an_inviter_may_revoke_inside_their_own_subtree(self):
        self.assertTrue(revocation.may_revoke(self.rita, self.identity(self.nils)))

    def test_nobody_may_revoke_an_ancestor(self):
        self.assertFalse(revocation.may_revoke(self.ali, self.identity(self.rita)))

    def test_nobody_may_revoke_across_branches(self):
        other = person("otto", ["reviewer"], sponsor=self.maria)
        cousin = person("cass", ["author"], sponsor=other)

        self.assertFalse(revocation.may_revoke(self.rita, self.identity(cousin)))

    def test_a_root_may_revoke_anywhere(self):
        self.assertTrue(revocation.may_revoke(self.root, self.identity(self.nils)))

    def test_a_root_may_not_revoke_another_root(self):
        """Roots are peers; removing one needs a second to agree (US-5.5)."""
        second = person("second", ["admin"], is_root=True)

        self.assertFalse(revocation.may_revoke(self.root, self.identity(second)))

    def test_nobody_may_revoke_themselves(self):
        """Standing down is US-5.3 and re-parents rather than cascades."""
        self.assertFalse(revocation.may_revoke(self.rita, self.identity(self.rita)))

    def test_a_suspended_account_may_not_revoke_anybody(self):
        identity = self.identity(self.rita)
        identity.trust_state = TrustState.SUSPENDED
        identity.save(update_fields=["trust_state"])

        # Re-read: creating the identity cached it on the user object, so the
        # one held here still says active. Every real caller loads the user
        # fresh from the session.
        rita = User.objects.get(pk=self.rita.pk)

        self.assertFalse(revocation.may_revoke(rita, self.identity(self.nils)))

    # -- the blast radius --------------------------------------------------

    def test_the_preview_names_everybody_the_cascade_reaches(self):
        blast = revocation.preview(self.root, self.identity(self.rita))

        self.assertEqual(
            sorted(node.user.username for node in blast.descendants), ["ali", "nils"]
        )
        self.assertEqual(blast.total, 3)

    def test_the_preview_matches_what_revoking_then_does(self):
        blast = revocation.preview(self.root, self.identity(self.rita))
        expected = {node.user.username for node in blast.descendants}

        self.revoke(self.root, self.rita)

        suspended = set(
            KeycloakIdentity.objects.filter(
                trust_state=TrustState.SUSPENDED
            ).values_list("user__username", flat=True)
        )
        self.assertEqual(suspended, expected)

    # -- what revocation does ----------------------------------------------

    def test_it_removes_the_roles_ends_sessions_and_disables_the_account(self):
        """Locally removing groups would be undone at the next sign-in."""
        self.revoke(self.root, self.rita)

        self.assertEqual(self.realm.removed, [("sub-rita", ["reviewer"])])
        self.assertEqual(self.realm.logged_out, ["sub-rita"])
        self.assertEqual(self.realm.disabled, ["sub-rita"])

    def test_the_revoked_account_is_deactivated_here(self):
        self.revoke(self.root, self.rita)

        self.rita.refresh_from_db()
        self.assertFalse(self.rita.is_active)

    def test_the_reason_is_required(self):
        with self.assertRaises(revocation.RevocationError):
            self.revoke(self.root, self.rita, reason="   ")

    def test_the_reason_is_recorded(self):
        self.revoke(self.root, self.rita, reason="shared their passkey")

        event = SsoAuditEvent.objects.get(
            action=SsoAuditEvent.Action.REVOKED_TRUST, username="rita"
        )
        self.assertIn("shared their passkey", str(event.detail))

    def test_directly_revoked_and_suspended_stay_distinguishable(self):
        self.revoke(self.root, self.rita)

        self.assertTrue(self.identity(self.rita).revoked_directly)
        self.assertFalse(self.identity(self.ali).revoked_directly)
        self.assertEqual(self.identity(self.ali).trust_state, TrustState.SUSPENDED)

    def test_a_suspended_account_keeps_its_realm_roles(self):
        """They have done nothing wrong; only their permissions here are held."""
        self.revoke(self.root, self.rita)

        self.assertNotIn("sub-ali", self.realm.disabled)
        self.assertEqual(self.realm.removed, [("sub-rita", ["reviewer"])])

    # -- the rule everything rests on --------------------------------------

    def test_signing_in_does_not_give_a_suspended_account_its_groups_back(self):
        """The regression test for the whole design.

        mirror_roles reconciles Django's groups from the token at every
        sign-in, so a suspension that only removed groups would last until the
        person next authenticated.
        """
        Group.objects.get_or_create(name="qgisfeedentry_authors")
        self.revoke(self.root, self.rita)
        ali = User.objects.get(pk=self.ali.pk)

        mirror_roles(ali, ["author"])

        ali.refresh_from_db()
        self.assertFalse(ali.is_staff)
        self.assertEqual(list(ali.groups.values_list("name", flat=True)), [])

    def test_a_revoked_subject_is_refused_at_the_callback(self):
        """Belt and braces: the realm account is disabled, but somebody may
        re-enable it in the console without knowing what it meant here."""
        from ..auth import QGISOIDCAuthenticationBackend

        self.revoke(self.root, self.rita)
        backend = QGISOIDCAuthenticationBackend()
        rita = User.objects.get(pk=self.rita.pk)

        with self.assertRaises(SuspiciousOperation):
            backend.refuse_if_revoked(rita, {"sub": "sub-rita"})

    def test_a_suspended_subject_may_still_sign_in(self):
        """US-5.2 is explicit: they can sign in and see why."""
        from ..auth import QGISOIDCAuthenticationBackend

        self.revoke(self.root, self.rita)
        backend = QGISOIDCAuthenticationBackend()
        ali = User.objects.get(pk=self.ali.pk)

        backend.refuse_if_revoked(ali, {"sub": "sub-ali"})

    # -- giving it back ----------------------------------------------------

    def test_restoring_brings_the_subtree_back_in_one_action(self):
        self.revoke(self.root, self.rita)

        revocation.restore(self.root, self.identity(self.rita), client=self.realm)

        for user in (self.rita, self.ali, self.nils):
            self.assertEqual(self.identity(user).trust_state, TrustState.ACTIVE)
        self.rita.refresh_from_db()
        self.assertTrue(self.rita.is_active)

    def test_restoring_puts_the_recorded_roles_back(self):
        self.revoke(self.root, self.rita)

        revocation.restore(self.root, self.identity(self.rita), client=self.realm)

        self.assertIn(("sub-rita", ["reviewer"]), self.realm.assigned)
        self.assertIn("sub-rita", self.realm.enabled)

    def test_restoring_is_refused_once_the_window_has_passed(self):
        self.revoke(self.root, self.rita)
        identity = self.identity(self.rita)
        identity.revoked_at = timezone.now() - timedelta(days=8)
        identity.save(update_fields=["revoked_at"])

        with self.assertRaises(revocation.RevocationError):
            revocation.restore(self.root, identity, client=self.realm)

    def test_restoring_leaves_alone_anything_suspended_by_something_else(self):
        other = person("otto", ["reviewer"], sponsor=self.maria)
        self.revoke(self.root, other)
        self.revoke(self.root, self.rita)

        revocation.restore(self.root, self.identity(self.rita), client=self.realm)

        self.assertEqual(self.identity(other).trust_state, TrustState.REVOKED)


@override_settings(
    **dict(REVOKE_SETTINGS, SSO_ON_REVOKE="qgis_sso.tests.test_revocation.record_hook")
)
class HookTest(TestCase):
    """What the site does about the revoked person's unpublished work."""

    def setUp(self):
        self.realm = InvitingRealm()
        self.root = person("root", ["admin"], is_root=True)
        self.root.is_superuser = True
        self.root.save(update_fields=["is_superuser"])
        self.rita = person("rita", ["reviewer"], sponsor=self.root)
        called.clear()

    def test_the_hook_is_called_once_with_the_revoked_user(self):
        revocation.revoke(
            self.root,
            KeycloakIdentity.objects.get(user=self.rita),
            "reason",
            client=self.realm,
        )

        self.assertEqual(called, [self.rita.pk])

    @override_settings(SSO_ON_REVOKE="qgis_sso.tests.test_revocation.exploding_hook")
    def test_a_hook_that_raises_takes_the_whole_revocation_back(self):
        """Otherwise a revocation is recorded that the site never acted on."""
        with self.assertRaises(RuntimeError):
            revocation.revoke(
                self.root,
                KeycloakIdentity.objects.get(user=self.rita),
                "reason",
                client=self.realm,
            )

        self.assertEqual(
            KeycloakIdentity.objects.get(user=self.rita).trust_state,
            TrustState.ACTIVE,
        )


called = []


def record_hook(user):
    called.append(user.pk)


def exploding_hook(user):
    raise RuntimeError("the site's hook failed")


@override_settings(**REVOKE_SETTINGS)
class RevokePageTest(TestCase):
    """The confirmation page, which is where a revocation is actually done."""

    def setUp(self):
        self.realm = InvitingRealm()
        self.root = person("root", ["admin"], is_root=True)
        self.root.is_superuser = True
        self.root.save(update_fields=["is_superuser"])
        self.rita = person("rita", ["reviewer"], sponsor=self.root)
        self.ali = person("ali", ["author"], sponsor=self.rita)
        self.outsider = person("otto", ["author"], sponsor=self.root)
        self.client.force_login(
            self.root, backend="django.contrib.auth.backends.ModelBackend"
        )

    def url(self, user):
        return reverse(
            "qgis_sso:revoke", args=[KeycloakIdentity.objects.get(user=user).pk]
        )

    def test_it_names_everybody_the_cascade_would_reach(self):
        response = self.client.get(self.url(self.rita))

        self.assertContains(response, "rita")
        self.assertContains(response, "ali")
        self.assertNotContains(response, "otto")

    def test_looking_at_it_changes_nothing(self):
        """It is a GET, so arriving, reading and leaving is safe."""
        self.client.get(self.url(self.rita))

        self.assertEqual(
            KeycloakIdentity.objects.get(user=self.rita).trust_state,
            TrustState.ACTIVE,
        )

    def test_a_reason_is_required(self):
        response = self.client.post(self.url(self.rita), {"reason": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            KeycloakIdentity.objects.get(user=self.rita).trust_state,
            TrustState.ACTIVE,
        )

    def test_somebody_who_may_not_revoke_cannot_even_see_the_page(self):
        """One answer for 'no such account' and 'not yours', so the page cannot
        be used to find out who exists."""
        self.client.force_login(
            self.ali, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = self.client.get(self.url(self.rita))

        self.assertEqual(response.status_code, 302)

    def test_an_unknown_identity_reads_the_same_as_a_forbidden_one(self):
        forbidden = self.client.get(self.url(self.rita))
        self.client.force_login(
            self.ali, backend="django.contrib.auth.backends.ModelBackend"
        )
        missing = self.client.get(reverse("qgis_sso:revoke", args=[999999]))

        self.assertEqual(missing.status_code, 302)
        self.assertEqual(forbidden.status_code, 200)

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        self.client.logout()

        response = self.client.get(self.url(self.rita))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


@override_settings(**REVOKE_SETTINGS)
class RevokedAccessTest(TestCase):
    """What a revoked or suspended account can still reach.

    All of these were reachable at one point. A revocation that removes the
    realm roles but leaves the person signed in here, holding their Django
    permissions and their quota, has withdrawn nothing that matters.
    """

    def setUp(self):
        self.realm = InvitingRealm()
        self.root = person("root", ["admin"], is_root=True)
        self.root.is_superuser = True
        self.root.save(update_fields=["is_superuser"])
        self.rita = person("rita", ["reviewer"], sponsor=self.root)
        self.ali = person("ali", ["author"], sponsor=self.rita)

    def revoke_rita(self):
        revocation.revoke(
            self.root,
            KeycloakIdentity.objects.get(user=self.rita),
            "reason",
            client=self.realm,
        )

    def test_revocation_takes_the_local_permissions_too(self):
        """Mirroring only runs at sign-in, and a revoked account never has
        another one, so leaving them was leaving them for good."""
        Group.objects.get_or_create(name="qgisfeedentry_authors")
        self.rita.is_staff = True
        self.rita.save(update_fields=["is_staff"])

        self.revoke_rita()

        self.rita.refresh_from_db()
        self.assertFalse(self.rita.is_staff)
        self.assertFalse(self.rita.is_superuser)
        self.assertEqual(list(self.rita.groups.all()), [])

    def test_a_revoked_account_can_no_longer_invite(self):
        """The roles Keycloak knew about are gone, but the tier is read from
        last_seen_roles, which a revoked account never refreshes."""
        self.revoke_rita()

        self.rita.refresh_from_db()
        self.assertEqual(tiers.invitable_roles(self.rita), [])
        self.assertEqual(tiers.quota(self.rita), 0)

    def test_a_suspended_account_can_no_longer_invite_either(self):
        self.revoke_rita()

        self.ali.refresh_from_db()
        self.assertEqual(tiers.invitable_roles(self.ali), [])

    def test_an_open_session_dies_at_the_next_request(self):
        """Disabling the realm account only stops the next sign-in, and
        somebody already signed in never makes one."""
        self.client.force_login(
            self.rita, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.assertEqual(self.client.get(reverse("qgis_sso:profile")).status_code, 200)

        self.revoke_rita()

        response = self.client.get(reverse("qgis_sso:profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_the_backend_refuses_to_resolve_a_revoked_session(self):
        """mozilla-django-oidc's get_user does not check is_active, so every
        page would have gone on serving them."""
        from ..auth import QGISOIDCAuthenticationBackend

        self.revoke_rita()

        self.assertIsNone(QGISOIDCAuthenticationBackend().get_user(self.rita.pk))

    def test_a_suspended_account_may_look_but_not_act(self):
        """US-5.2 says they can sign in and see why; nothing more."""
        self.revoke_rita()
        self.client.force_login(
            self.ali, backend="django.contrib.auth.backends.ModelBackend"
        )

        self.assertEqual(self.client.get(reverse("qgis_sso:profile")).status_code, 200)

        invite = self.client.get(reverse("qgis_sso:invite_new"))
        self.assertEqual(invite.status_code, 302)

    def test_restoring_gives_the_roles_and_permissions_back(self):
        Group.objects.get_or_create(name="qgisfeedentry_authors")
        self.revoke_rita()

        revocation.restore(
            self.root,
            KeycloakIdentity.objects.get(user=self.rita),
            client=self.realm,
        )

        self.rita.refresh_from_db()
        self.assertTrue(self.rita.is_active)
        self.assertEqual(
            KeycloakIdentity.objects.get(user=self.rita).last_seen_roles, ["reviewer"]
        )
        self.assertEqual(tiers.effective_tier(self.rita), 2)
