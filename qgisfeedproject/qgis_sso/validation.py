# coding=utf-8
"""The username as the realm stores it."""


def clean_username(value):
    """The username as it will be stored, here and in the realm.

    Keycloak stores usernames in lower case, so we do too.
    """
    return value.strip().lower()
