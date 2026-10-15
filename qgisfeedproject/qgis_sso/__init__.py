# coding=utf-8
"""Keycloak single sign-on for QGIS sites.

Deliberately feed-agnostic: nothing in this app imports from ``qgisfeed``, and
the mapping from Keycloak roles to Django groups and flags is supplied by
settings rather than hard-coded here, so a second QGIS site can reuse the app
with its own role map.

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""
