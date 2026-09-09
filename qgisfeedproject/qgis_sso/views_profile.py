# coding=utf-8
"""The profile page, and the sign-in request that carries a Keycloak action.

The page is here; the passkey ceremony is not, and cannot be. WebAuthn binds a
credential to the relying party that created it, so only ``auth.qgis.org`` can
register or delete one. Pressing a button here starts an ordinary sign-in
carrying ``kc_action``, Keycloak runs the action, and the user comes back.
"""

from django.contrib import messages
from django.contrib.auth import SESSION_KEY
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache
from mozilla_django_oidc.views import OIDCAuthenticationRequestView

from .keycloak import KeycloakError
from .passkeys import ACTION_SESSION_KEY, REGISTER, delete_action, passkeys_for

ADD_PASSKEY = "add-passkey"
REMOVE_PASSKEY = "remove-passkey"


def signed_in(request):
    """Whether a real person is signed in.

    Read from the session, not ``request.user``: ``QgisFeedUserVisitMiddleware``
    substitutes a shared ``qgis_user`` account on anonymous requests, so being
    authenticated proves nothing.
    """
    return bool(request.session.get(SESSION_KEY))


@method_decorator(never_cache, name="dispatch")
class ProfileView(View):
    """Somebody's own account: who they are here, and their passkeys."""

    template_name = "qgis_sso/profile.html"

    def dispatch(self, request, *args, **kwargs):
        if not signed_in(request):
            return HttpResponseRedirect(
                f"{reverse('login')}?next={reverse('qgis_sso:profile')}"
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, self.page_context(request))

    def post(self, request):
        """Hand the user to Keycloak to change a credential.

        Nothing is changed here. The action is worked out from the request,
        checked, and left in the session for the sign-in request to pick up, so
        that a link cannot name an action of its own.
        """
        identity = getattr(request.user, "keycloak_identity", None)
        if identity is None:
            messages.error(
                request, _("This account does not sign in through auth.qgis.org.")
            )
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        action = request.POST.get("action", "")
        if action == ADD_PASSKEY:
            return self.start(request, REGISTER)
        if action == REMOVE_PASSKEY:
            return self.remove(request, identity)

        messages.error(request, _("Unknown action."))
        return HttpResponseRedirect(reverse("qgis_sso:profile"))

    def remove(self, request, identity):
        """Delete one passkey, once it is known to be theirs and not their last.

        Removing the only one leaves an account with no way in at all: these
        accounts have no password, and the setup link an administrator could
        send is refused for anybody who has already signed in.
        """
        try:
            passkeys = passkeys_for(identity)
        except KeycloakError as error:
            messages.error(request, _("Could not reach auth.qgis.org: %s") % error)
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        wanted = request.POST.get("credential", "")
        if wanted not in {passkey.id for passkey in passkeys}:
            # Checked against their own credentials, so one person cannot name
            # another's and have Keycloak asked about it.
            messages.error(request, _("That passkey is not on your account."))
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        if len(passkeys) == 1:
            messages.error(
                request,
                _(
                    "This is your only passkey. Enrol another one first, or you "
                    "will be locked out: there is no password on this account."
                ),
            )
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        return self.start(request, delete_action(wanted))

    @staticmethod
    def start(request, action):
        """Begin a sign-in that asks Keycloak to run ``action`` on the way."""
        request.session[ACTION_SESSION_KEY] = action
        target = reverse("oidc_authentication_init")
        return HttpResponseRedirect(f"{target}?next={reverse('qgis_sso:profile')}")

    def page_context(self, request):
        identity = getattr(request.user, "keycloak_identity", None)
        context = {
            "identity": identity,
            "groups": request.user.groups.all(),
            "passkeys": [],
            "unreachable": False,
        }
        if identity is None:
            return context

        try:
            context["passkeys"] = passkeys_for(identity)
        except KeycloakError:
            # The rest of the page is still worth showing, and the realm being
            # briefly unreachable is not the user's problem to solve.
            context["unreachable"] = True
        return context


class ActionAuthenticationRequestView(OIDCAuthenticationRequestView):
    """The sign-in request, plus a ``kc_action`` when the profile page set one.

    The action is taken from the session rather than the query string. A view
    puts it there after checking it, so this cannot be pointed at an arbitrary
    Keycloak action by anybody who can get a browser to follow a link.
    """

    def get_extra_params(self, request):
        params = super().get_extra_params(request)
        action = request.session.pop(ACTION_SESSION_KEY, None)
        if action:
            params["kc_action"] = action
        return params
