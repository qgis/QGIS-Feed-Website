# coding=utf-8
"""The profile page, and the sign-in request that carries a Keycloak action.

Only ``auth.qgis.org`` can register or delete a passkey. A button here starts
an ordinary sign-in carrying ``kc_action``, Keycloak runs the action, and the
user comes back.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache
from mozilla_django_oidc.views import OIDCAuthenticationRequestView

from .keycloak import KeycloakError
from .models import KeycloakIdentity
from .passkeys import ACTION_SESSION_KEY, REGISTER, delete_action, passkeys_for

logger = logging.getLogger(__name__)

ADD_PASSKEY = "add-passkey"
REMOVE_PASSKEY = "remove-passkey"


def signed_in(request: HttpRequest) -> bool:
    """Whether a real person is signed in, right now.

    ``request.user`` alone proves nothing, because ``QgisFeedUserVisitMiddleware``
    puts a shared ``qgis_user`` on anonymous requests. The session key alone
    can outlive the account. So the two have to agree.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return str(user.pk) == str(request.session.get(SESSION_KEY))


def trusted(user: User) -> bool:
    """Whether this account may still change anything.

    A suspended account stays active and can sign in, so views ask this too.
    """
    identity = getattr(user, "keycloak_identity", None)
    return identity is None or identity.trusted


@method_decorator(never_cache, name="dispatch")
class ProfileView(View):
    """Somebody's own account: who they are here, and their passkeys."""

    template_name = "qgis_sso/profile.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not signed_in(request):
            return HttpResponseRedirect(
                f"{reverse('login')}?next={reverse('qgis_sso:profile')}"
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self.page_context(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        """Hand the user to Keycloak to change a credential.

        Nothing changes here. The checked action waits in the session for the
        sign-in request, so a link cannot name an action of its own.
        """
        identity = getattr(request.user, "keycloak_identity", None)
        if identity is None:
            messages.error(
                request, _("This account does not sign in with a QGIS account.")
            )
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        action = request.POST.get("action", "")
        if action == ADD_PASSKEY:
            return self.start(request, REGISTER)
        if action == REMOVE_PASSKEY:
            return self.remove(request, identity)

        messages.error(request, _("That action is not available here."))
        return HttpResponseRedirect(reverse("qgis_sso:profile"))

    def remove(self, request: HttpRequest, identity: KeycloakIdentity) -> HttpResponse:
        """Delete one passkey, once it is known to be theirs and not their last.

        These accounts have no password, so removing the last passkey would
        lock the person out.
        """
        try:
            passkeys = passkeys_for(identity)
        except KeycloakError:
            logger.exception("Could not list passkeys for %s", identity.sub)
            messages.error(
                request,
                _(
                    "We could not reach the QGIS account service. Try again in a moment."
                ),
            )
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        wanted = request.POST.get("credential", "")
        if wanted not in {passkey.id for passkey in passkeys}:
            # So nobody can have Keycloak asked about another person's credential.
            messages.error(request, _("That passkey is not on your account."))
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        if len(passkeys) == 1:
            messages.error(
                request,
                _(
                    "This is your only passkey. Add another one first, or you "
                    "will be locked out. There is no password on this account."
                ),
            )
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        return self.start(request, delete_action(wanted))

    @staticmethod
    def permissions(user: User) -> list[str]:
        """What this account may do, in words rather than group names."""
        named = getattr(settings, "SSO_GROUP_LABELS", {})
        return [named.get(group.name, group.name) for group in user.groups.all()]

    @staticmethod
    def start(request: HttpRequest, action: str) -> HttpResponse:
        """Begin a sign-in that asks Keycloak to run ``action`` on the way."""
        request.session[ACTION_SESSION_KEY] = action
        target = reverse("oidc_authentication_init")
        return HttpResponseRedirect(f"{target}?next={reverse('qgis_sso:profile')}")

    def page_context(self, request: HttpRequest) -> dict[str, object]:
        """What the page shows without asking Keycloak anything.

        The passkey list comes later from :class:`PasskeyListView`, so a slow
        realm never holds up the rest of the page.
        """
        identity = getattr(request.user, "keycloak_identity", None)
        return {
            "identity": identity,
            "permissions": self.permissions(request.user),
        }


@method_decorator(never_cache, name="dispatch")
class PasskeyListView(View):
    """The passkey list on its own, for the profile page to fetch.

    It reads the account from the session and never from the URL.
    """

    template_name = "qgis_sso/_passkeys.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not signed_in(request):
            return HttpResponseForbidden()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> HttpResponse:
        identity = getattr(request.user, "keycloak_identity", None)
        context = {"passkeys": [], "unreachable": False}
        if identity is not None:
            try:
                context["passkeys"] = passkeys_for(identity)
            except KeycloakError:
                logger.exception("Could not list passkeys for %s", identity.sub)
                context["unreachable"] = True
        return render(request, self.template_name, context)


class ActionAuthenticationRequestView(OIDCAuthenticationRequestView):
    """The sign-in request, plus a ``kc_action`` when the profile page set one.

    The action comes from the session and never from the query string.
    """

    def get_extra_params(self, request: HttpRequest) -> dict[str, str]:
        params = super().get_extra_params(request)
        action = request.session.pop(ACTION_SESSION_KEY, None)
        if action:
            params["kc_action"] = action
        return params
