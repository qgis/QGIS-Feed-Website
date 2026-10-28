# coding=utf-8
"""What a wave of invitations costs, in round trips and in queries.

Deciding about one person is a read against the realm, and a wave used to make
those reads one after another while somebody watched a blank page. They now run
together. These tests pin down the three things that made that safe to do: the
order of the answers, what happens when a worker fails, and the promise that
nothing in the decision path touches the database.

They also pin the caching. The feed client's UUID and role list are the same
answer every time until we deploy a role, and paying two round trips for them on
every button press was most of the cost of a small wave.
"""

import threading
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..actions import inspect_concurrently, plan_provisioning
from ..keycloak import KeycloakError
from ..provisioning import Provisioner, clear_client_cache
from .base import FakeRealm

SETTINGS = {
    "OIDC_RP_CLIENT_ID": "feed-qgis-org",
    "SSO_SETUP_REDIRECT_URI": "https://feed.example.org/accounts/login/",
}


class CountingRealm(FakeRealm):
    """A realm that records how often the catalogue was asked for."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.uuid_calls = 0
        self.role_calls = 0

    def client_uuid(self, client_id):
        self.uuid_calls += 1
        return super().client_uuid(client_id)

    def client_roles(self, client_uuid):
        self.role_calls += 1
        return super().client_roles(client_uuid)


@override_settings(**SETTINGS)
class ConcurrentInspectTest(TestCase):
    """The fan-out itself, away from any view."""

    def test_answers_come_back_in_the_order_they_were_asked(self):
        """The confirmation screen lists people in the order they were picked.

        Completion order is whatever the realm happens to answer first, so
        anything that iterates as results arrive would shuffle the table.
        """
        names = [f"user-{index}" for index in range(12)]

        # Answer the later ones first, so a shuffle would be visible.
        def decide(name):
            index = int(name.split("-")[1])
            if index % 2:
                # Hold the odd ones back a little.
                threading.Event().wait(0.01)
            return name

        self.assertEqual(inspect_concurrently(decide, names), names)

    def test_a_realm_failure_reaches_the_caller(self):
        """A worker that raises must not be swallowed into a missing row.

        The view turns this into "we could not reach the QGIS account service".
        If it were dropped, the person would simply be absent from the
        confirmation table with nothing said about them.
        """

        def decide(name):
            if name == "user-3":
                raise KeycloakError("realm went away")
            return name

        with self.assertRaises(KeycloakError):
            inspect_concurrently(decide, [f"user-{index}" for index in range(8)])

    def test_one_user_is_decided_without_a_thread_pool(self):
        """A pool for a single lookup is cost with nothing to show for it."""
        seen = []

        def decide(name):
            seen.append(threading.current_thread().name)
            return name

        inspect_concurrently(decide, ["only"])

        self.assertEqual(seen, [threading.current_thread().name])


@override_settings(**SETTINGS)
class ClientCatalogueCacheTest(TestCase):
    """The client UUID and role list, which change on deploy and not on use."""

    def setUp(self):
        # FakeRealm clears the cache as it is built, so build it first and let
        # each test start from cold deliberately.
        self.realm = CountingRealm()

    def test_a_second_provisioner_does_not_ask_the_realm_again(self):
        Provisioner(client=self.realm)
        Provisioner(client=self.realm)

        self.assertEqual(self.realm.uuid_calls, 1)
        self.assertEqual(self.realm.role_calls, 1)

    def test_clearing_the_cache_makes_it_ask_again(self):
        """Deploying a role has to be visible without restarting the workers."""
        Provisioner(client=self.realm)
        clear_client_cache()
        Provisioner(client=self.realm)

        self.assertEqual(self.realm.uuid_calls, 2)
        self.assertEqual(self.realm.role_calls, 2)

    def test_the_catalogue_is_still_correct_when_it_comes_from_the_cache(self):
        first = Provisioner(client=self.realm)
        second = Provisioner(client=self.realm)

        self.assertEqual(second.client_uuid, first.client_uuid)
        self.assertEqual(second.available_roles, first.available_roles)


@override_settings(**SETTINGS)
class PlanningCostTest(TestCase):
    """Planning a wave, and the queries it is allowed to make."""

    def setUp(self):
        self.realm = FakeRealm()
        self.users = [
            User.objects.create_user(
                f"user{index}",
                f"user{index}@example.org",
                "x",
                last_login=timezone.now(),
            )
            for index in range(10)
        ]

    def plan(self, users):
        with mock.patch(
            "qgis_sso.provisioning.KeycloakAdminClient", return_value=self.realm
        ):
            return plan_provisioning(users)

    def test_planning_keeps_the_order_it_was_given(self):
        chosen = list(reversed(self.users))

        _session, decisions = self.plan(chosen)

        self.assertEqual(
            [decision.user.username for decision in decisions],
            [user.username for user in chosen],
        )

    def test_the_query_count_does_not_grow_with_the_wave(self):
        """Every lookup the decision needs is resolved once, up front.

        This is not only about speed. The decisions run across a thread pool,
        and a query issued from a worker opens a connection of its own. Holding
        the count flat is what keeps that from happening.
        """
        with CaptureQueriesContext(connection) as small:
            self.plan(self.users[:2])

        with CaptureQueriesContext(connection) as large:
            self.plan(self.users)

        # The exact number is an implementation detail and would make this a
        # tripwire for unrelated changes. That it does not move with the size
        # of the wave is the property worth holding.
        self.assertEqual(len(large), len(small))
