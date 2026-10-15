# coding=utf-8
"""The field rules, away from any view.

They exist to say here what the realm would say later, so the cases that matter
are the boundaries between the two: a name Keycloak refuses has to be refused,
and a name it accepts has to be accepted. Refusing more than the realm does
would be our own rule, invisible to the reader and absent from the docs.
"""

from django.test import SimpleTestCase

from .. import validation

#: Names Keycloak's default user profile accepts.
ALLOWED = ["ana.silva", "jo_99", "tim+qgis", "maría", "abc", "a" * 255]

#: Names it refuses, and why.
REFUSED = [
    ("ana silva", "cannot contain spaces"),
    ("ab", "at least three characters"),
    ("", "at least three characters"),
    ("a" * 256, "too long"),
    ("o'brien", "cannot contain spaces"),
    ('a"b', "cannot contain spaces"),
    ("a<b", "cannot contain spaces"),
    ("a&b", "cannot contain spaces"),
    ("a\tb", "cannot contain spaces"),
    ("a\x00b", "cannot contain spaces"),
]


class UsernameTest(SimpleTestCase):
    def test_a_name_the_realm_accepts_passes(self):
        for name in ALLOWED:
            with self.subTest(name=name):
                self.assertIsNone(validation.username_error(name))

    def test_a_name_the_realm_refuses_is_refused_here(self):
        for name, expected in REFUSED:
            with self.subTest(name=name):
                problem = validation.username_error(name)

                self.assertIsNotNone(problem)
                self.assertIn(expected, problem)

    def test_the_message_names_the_characters(self):
        """So the reader can see which one of theirs is the problem."""
        problem = validation.username_error("a<b")

        for character in ("<", ">", "&", '"', "'"):
            self.assertIn(character, problem)


class CleaningTest(SimpleTestCase):
    def test_it_lowercases_and_trims(self):
        self.assertEqual(validation.clean_username("  NewBie \n"), "newbie")

    def test_an_already_clean_name_is_unchanged(self):
        self.assertEqual(validation.clean_username("ana.silva"), "ana.silva")


class PersonNameTest(SimpleTestCase):
    def test_a_name_with_a_space_is_fine(self):
        """Keycloak allows whitespace in a name, unlike a username."""
        self.assertIsNone(validation.person_name_error("Ana Silva", "first name"))

    def test_an_empty_name_is_fine(self):
        """Both are optional, here and in the realm."""
        self.assertIsNone(validation.person_name_error("", "last name"))

    def test_a_prohibited_character_is_refused(self):
        problem = validation.person_name_error('Ana"', "first name")

        self.assertIn("first name", problem)

    def test_too_long_is_refused(self):
        problem = validation.person_name_error("a" * 256, "last name")

        self.assertIn("too long", problem)


class EmailTest(SimpleTestCase):
    def test_an_ordinary_address_is_fine(self):
        self.assertIsNone(validation.email_error("ana@example.org"))

    def test_too_long_is_refused(self):
        self.assertIsNotNone(validation.email_error("a" * 250 + "@example.org"))
