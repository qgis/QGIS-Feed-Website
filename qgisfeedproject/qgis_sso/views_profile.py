# coding=utf-8
"""The profile page, and the sign-in request that carries a Keycloak action.

The page is here; the passkey ceremony is not, and cannot be. WebAuthn binds a
credential to the relying party that created it, so only ``auth.qgis.org`` can
register or delete one. Pressing a button here starts an ordinary sign-in
carrying ``kc_action``, Keycloak runs the action, and the user comes back.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import SESSION_KEY
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache
from mozilla_django_oidc.views import OIDCAuthenticationRequestView

from . import revocation, sponsorship
from .keycloak import KeycloakError
from .passkeys import ACTION_SESSION_KEY, REGISTER, delete_action, passkeys_for

ADD_PASSKEY = "add-passkey"
REMOVE_PASSKEY = "remove-passkey"
ACCEPT_SPONSORSHIP = "accept-sponsorship"
DECLINE_SPONSORSHIP = "decline-sponsorship"
WITHDRAW_SPONSORSHIP = "withdraw-sponsorship"

#: The three offer actions, which a suspended account must be able to reach.
#: Everything else on this page needs standing; accepting a new sponsor is how
#: somebody gets their standing back.
OFFER_ACTIONS = (ACCEPT_SPONSORSHIP, DECLINE_SPONSORSHIP, WITHDRAW_SPONSORSHIP)


def signed_in(request):
    """Whether a real person is signed in, right now.

    Neither half of this is enough on its own. ``request.user`` proves nothing,
    because ``QgisFeedUserVisitMiddleware`` substitutes a shared ``qgis_user``
    account on anonymous requests. And the session key outlives the account:
    when a backend refuses to resolve it - a revoked user, say - Django leaves
    the key in place, because it only flushes the session when the *auth hash*
    fails, not when ``get_user`` returns None.

    So the two have to agree. A revoked account leaves a key pointing at
    nobody, ``request.user`` becomes the shared account, and the mismatch is
    what catches it.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return str(user.pk) == str(request.session.get(SESSION_KEY))


def trusted(user):
    """Whether this account may still change anything.

    A suspended account stays active and can sign in, which US-5.2 asks for.
    Nothing else about it should work, and each view remembering that on its
    own is how one of them forgets.
    """
    identity = getattr(user, "keycloak_identity", None)
    return identity is None or identity.trusted


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
                request, _("This account does not sign in with a QGIS account.")
            )
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        action = request.POST.get("action", "")
        if action in OFFER_ACTIONS:
            return self.answer_offer(request, identity, action)
        if action == ADD_PASSKEY:
            return self.start(request, REGISTER)
        if action == REMOVE_PASSKEY:
            return self.remove(request, identity)

        messages.error(request, _("Unknown action."))
        return HttpResponseRedirect(reverse("qgis_sso:profile"))

    def answer_offer(self, request, identity, action):
        """Accept, decline or withdraw one offer to sponsor somebody.

        The offer is resolved against the reader's own two lists rather than
        read by id, so a hand made POST cannot answer somebody else's.
        """
        wanted = request.POST.get("offer", "")
        if action == WITHDRAW_SPONSORSHIP:
            waiting = sponsorship.pending_by(request.user)
        else:
            waiting = sponsorship.pending_for(identity)
        made = waiting.filter(pk=wanted).first() if wanted.isdigit() else None
        if made is None:
            messages.error(request, _("That offer is no longer waiting."))
            return HttpResponseRedirect(reverse("qgis_sso:profile"))

        try:
            if action == ACCEPT_SPONSORSHIP:
                rescued = sponsorship.accept(request.user, made)
                messages.success(
                    request,
                    _("%(sponsor)s is your sponsor now, with %(count)d other(s).")
                    % {
                        "sponsor": made.sponsor.username,
                        # This account is in the list, so it is not "other".
                        "count": max(0, len(rescued) - 1),
                    },
                )
            elif action == DECLINE_SPONSORSHIP:
                sponsorship.decline(request.user, made)
                messages.success(request, _("We told them no. Nothing has changed."))
            else:
                sponsorship.withdraw(request.user, made)
                messages.success(request, _("We took your offer back."))
        except revocation.RevocationError as error:
            messages.error(request, str(error))
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
            messages.error(
                request,
                _("We could not reach the QGIS account service. Try again in a moment.")
                + f" ({error})",
            )
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
    def permissions(user):
        """What this account may do, said plainly.

        Group names are database identifiers. A contributor reading their own
        account should see what the group lets them do instead.
        """
        named = getattr(settings, "SSO_GROUP_LABELS", {})
        return [named.get(group.name, group.name) for group in user.groups.all()]

    @staticmethod
    def start(request, action):
        """Begin a sign-in that asks Keycloak to run ``action`` on the way."""
        request.session[ACTION_SESSION_KEY] = action
        target = reverse("oidc_authentication_init")
        return HttpResponseRedirect(f"{target}?next={reverse('qgis_sso:profile')}")

    def page_context(self, request):
        """What the page can show without asking Keycloak anything.

        The passkey list is deliberately absent. Fetching it is two round trips
        to the realm, and holding the render for them means a slow realm delays
        the name and the permissions too, which are already here. The page
        paints, then :class:`PasskeyListView` fills the list in.
        """
        identity = getattr(request.user, "keycloak_identity", None)
        return {
            "identity": identity,
            "permissions": self.permissions(request.user),
            "offers": sponsorship.pending_for(identity) if identity else [],
            "offers_made": sponsorship.pending_by(request.user),
        }


@method_decorator(never_cache, name="dispatch")
class PasskeyListView(View):
    """The passkey list on its own, for the profile page to fetch.

    Whose passkeys is never a parameter. It is read from the signed in account,
    so this cannot be pointed at somebody else's by changing a URL.
    """

    template_name = "qgis_sso/_passkeys.html"

    def dispatch(self, request, *args, **kwargs):
        if not signed_in(request):
            return HttpResponseForbidden()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        identity = getattr(request.user, "keycloak_identity", None)
        context = {"passkeys": [], "unreachable": False}
        if identity is not None:
            try:
                context["passkeys"] = passkeys_for(identity)
            except KeycloakError:
                # The realm being briefly unreachable is not the reader's
                # problem to solve, and the rest of the page still stands.
                context["unreachable"] = True
        return render(request, self.template_name, context)


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
