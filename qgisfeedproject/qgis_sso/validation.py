# coding=utf-8
"""What the realm will accept in a name, checked before we ask it.

Keycloak validates every field of a user profile on its own side. When our
invite form is laxer than the realm, the account is refused at the last step and
the reader is told the service is unavailable, which is untrue and gives them
nothing to fix. So the rules live here and the form applies them first.

These mirror Keycloak's default user profile:

=================  ==========================================================
``username``       ``length`` 3 to 255, and ``username-prohibited-characters``
``firstName``      ``length`` up to 255, and ``person-name-prohibited-characters``
``lastName``       the same as ``firstName``
``email``          ``length`` up to 255
=================  ==========================================================

Both prohibited-character validators refuse ``< > & " '`` and control
characters. The username one refuses whitespace as well, which a name may
contain. We leave ``up-username-not-idn-homograph`` to the realm, because it
depends on Unicode tables.
"""

import re

from django.utils.translation import gettext as _

USERNAME_MIN = 3
MAX_LENGTH = 255

#: Characters neither a username nor a name may contain, with Java's \p{Cntrl}
#: spelled out: the ASCII controls and delete.
PROHIBITED = r'<>&"\'\x00-\x1f\x7f'

USERNAME = re.compile(rf"^[^{PROHIBITED}\s]+$")
PERSON_NAME = re.compile(rf"^[^{PROHIBITED}]+$")


def clean_username(value: str) -> str:
    """The username as it will be stored, here and in the realm.

    Keycloak stores usernames in lower case, so we do too.
    """
    return value.strip().lower()


def username_error(value: str) -> str | None:
    """Why the realm would refuse this username, or None."""
    if len(value) < USERNAME_MIN:
        return _("A username needs at least three characters.")
    if len(value) > MAX_LENGTH:
        return _("That username is too long. Use 255 characters or fewer.")
    if not USERNAME.match(value):
        return _(
            "A username cannot contain spaces, or any of < > & \" '. "
            "Try a dot or an underscore instead."
        )
    return None


def person_name_error(value: str, label: str) -> str | None:
    """Why the realm would refuse this first or last name, or None.

    ``label`` names the field as the reader sees it.
    """
    if not value:
        return None
    if len(value) > MAX_LENGTH:
        return _("That %(label)s is too long. Use 255 characters or fewer.") % {
            "label": label,
        }
    if not PERSON_NAME.match(value):
        return _("A %(label)s cannot contain any of < > & \" '.") % {"label": label}
    return None


def email_error(value: str) -> str | None:
    """Why the realm would refuse this address, or None. Only the length.

    The browser's ``type="email"`` already checks the shape.
    """
    if len(value) > MAX_LENGTH:
        return _("That address is too long. Use 255 characters or fewer.")
    return None
