# coding=utf-8
""" "Configure QGIS News app, create user group and signals

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""

__author__ = "elpaso@itopen.it"
__date__ = "2019-05-08"
__copyright__ = "Copyright 2019, ItOpen"


from django.apps import AppConfig
from django.db.models.signals import post_save


class QgisFeedConfig(AppConfig):
    name = "qgisfeed"

    # Every model in this app already has a 32-bit AutoField primary key on
    # disk, so this states what the migrations built rather than changing it:
    # it silences models.W042 without generating an AlterField. Moving to
    # BigAutoField would rewrite six tables and every foreign key referencing
    # them, which is a data migration and needs deciding on its own merits.
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        from django.contrib.auth.models import User
        from user_visit.models import UserVisit

        from .signals import post_save_user_visit, setup_approver_group, setup_group

        post_save.connect(setup_group, sender=User)
        post_save.connect(setup_approver_group, sender=User)
        post_save.connect(post_save_user_visit, sender=UserVisit)
