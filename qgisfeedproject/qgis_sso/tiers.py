# coding=utf-8
"""What each role is called on screen, and what it lets a person do.

Both come from ``SSO_ROLE_LABELS`` and ``SSO_ROLE_SUMMARIES``, so another site
names its own roles without touching this module.
"""

from django.conf import settings


def label(role: str) -> str:
    """The on-screen name of a role, or its identifier if it has none."""
    return getattr(settings, "SSO_ROLE_LABELS", {}).get(role, role)


def summary(role: str) -> str:
    """One line on what the role lets a person do, or an empty string."""
    return getattr(settings, "SSO_ROLE_SUMMARIES", {}).get(role, "")
