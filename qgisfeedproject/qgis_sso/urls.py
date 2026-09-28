# coding=utf-8
"""Everything single sign-on serves, under ``/sso/``.

The login journey, the account's own profile, the help page, and the
maintenance screens. They share one namespace because they are one app.
``help/`` is open to anybody, because the reader who needs it most is often the
one who cannot sign in. Under ``manage/``, each view says for itself who may
use it: the list is open to anybody with a realm account and shows them their
own branch, inviting an existing user is for administrators, and withdrawing
trust, moving somebody to a new sponsor and offering to sponsor somebody each
have their own rule.

Every action that asks before it acts has a route here. Sending somebody their
setup link and cancelling an invitation act on one row, so they take its id;
each applies the same rule the list applied to the row, because a URL somebody
typed had no row to hide it.
"""

from django.urls import path

from . import views, views_manage, views_profile

app_name = "qgis_sso"

urlpatterns = [
    path("help/", views.HelpView.as_view(), name="help"),
    path("sign-in-failed/", views.sign_in_failed, name="sign_in_failed"),
    path("profile/", views_profile.ProfileView.as_view(), name="profile"),
    path(
        "profile/passkeys/",
        views_profile.PasskeyListView.as_view(),
        name="passkey_list",
    ),
    path("manage/", views_manage.EnrolmentView.as_view(), name="enrolment"),
    path(
        "manage/link/",
        views_manage.IssuedLinkView.as_view(),
        name="issued_link",
    ),
    path(
        "manage/send-email/<int:pk>/",
        views_manage.SendEmailView.as_view(),
        name="send_email",
    ),
    path(
        "manage/cancel-invitation/<int:pk>/",
        views_manage.CancelInvitationView.as_view(),
        name="cancel_invitation",
    ),
    path(
        "manage/invite/new/",
        views_manage.InviteNewView.as_view(),
        name="invite_new",
    ),
    path(
        "manage/invite/existing/",
        views_manage.InviteExistingView.as_view(),
        name="invite_existing",
    ),
    path(
        "manage/revoke/<int:pk>/",
        views_manage.RevokeView.as_view(),
        name="revoke",
    ),
    path(
        "manage/reparent/<int:pk>/",
        views_manage.ReparentView.as_view(),
        name="reparent",
    ),
    path(
        "manage/sponsor/<int:pk>/",
        views_manage.SponsorOfferView.as_view(),
        name="sponsor_offer",
    ),
]
