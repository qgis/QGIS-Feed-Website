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
contain.

One rule we do not mirror: ``up-username-not-idn-homograph``, which refuses a
username mixing scripts that can be read as another name. Reimplementing it
would mean keeping a second copy of a table that changes with the Unicode
version. A name it catches still fails at the realm, and the invite view reports
that as a realm failure.
"""

import re

from django.utils.translation import gettext as _

#: Keycloak's length validator on the username. The floor is what makes a
#: two-letter name a message here rather than a refusal at the realm.
USERNAME_MIN = 3

#: Every field here shares the same ceiling.
MAX_LENGTH = 255

#: Characters neither a username nor a name may contain. The control range is
#: spelled out because Python's ``re`` has no ``\p{Cntrl}``, and it covers what
#: Java means by that class: the ASCII controls and delete, nothing above.
PROHIBITED = r'<>&"\'\x00-\x1f\x7f'

USERNAME = re.compile(rf"^[^{PROHIBITED}\s]+$")
PERSON_NAME = re.compile(rf"^[^{PROHIBITED}]+$")


def clean_username(value):
    """The username as it will be stored, here and in the realm.

    Lowercased, because Keycloak treats usernames case insensitively and stores
    them in lower case. Doing it here means the account on this site and the
    account in the realm carry one name rather than two that differ by case.
    This is what :func:`qgis_sso.migration.proposed_username` already does for
    accounts that came from the migration.
    """
    return value.strip().lower()


def username_error(value):
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


def person_name_error(value, label):
    """Why the realm would refuse this first or last name, or None.

    ``label`` is the field as the reader sees it, so the message names the box
    they have to go back to.
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


def email_error(value):
    """Why the realm would refuse this address, or None.

    Length only. The shape is the browser's ``type="email"`` and Django's own
    field validation, and repeating either here would be a second opinion on
    something already settled.
    """
    if len(value) > MAX_LENGTH:
        return _("That address is too long. Use 255 characters or fewer.")
    return None
