# coding=utf-8
"""Views supporting the SSO login journey."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView

from .auth import REFUSAL_SESSION_KEY

logger = logging.getLogger(__name__)


def sign_in_failed(request):
    """Explain a failed sign-in without showing a traceback.

    ``mozilla-django-oidc`` sends every authentication failure to
    ``LOGIN_REDIRECT_URL_FAILURE``. By far the most common cause here is a
    valid realm user who has no account on this site, so that case is named
    explicitly; anything else falls back to a generic message rather than
    asserting a reason we do not know.
    """
    reason = request.session.pop(REFUSAL_SESSION_KEY, None)
    context = {
        "no_account": reason == "no-account",
        "support_url": getattr(settings, "SSO_SUPPORT_URL", ""),
    }
    # 403 rather than 200: the request was understood and refused, and it
    # keeps the page out of search results.
    return render(request, "qgis_sso/sign_in_failed.html", context, status=403)


def site_login(request, *args, **kwargs):
    """The site login page, minus the redirect loop for a signed-in user.

    ``qgisfeed`` guards its views with ``permission_required``, which sends a
    user who lacks the permission to ``LOGIN_URL`` with a ``next``. For an
    anonymous user that is right. For a user who is already signed in it is a
    loop: they authenticate, get bounced back here, and see a login form while
    the header offers them a logout.

    That combination was rare before, because every staff account was in the
    authors group. It is ordinary now: roles are mirrored from Keycloak at
    every sign-in, so an account with no feed role legitimately ends up signed
    in with no permissions. Say that, rather than showing the form again.
    """
    if request.user.is_authenticated and request.GET.get("next"):
        return render(
            request,
            "qgis_sso/sign_in_failed.html",
            {
                "no_permission": True,
                "support_url": getattr(settings, "SSO_SUPPORT_URL", ""),
            },
            status=403,
        )
    return LoginView.as_view()(request, *args, **kwargs)


def admin_login(request):
    """Send Django admin's own login form to the site login page.

    Django admin ships a second login view that authenticates against every
    configured backend. Left in place it would keep accepting local passwords
    after the site login page had stopped offering them, making the fallback
    wider than intended. Redirecting to the single site login page means one
    page enforces the policy.
    """
    target = reverse("login")
    next_url = request.GET.get("next")
    if next_url:
        # Only same-site destinations, so this cannot be turned into an open
        # redirect wearing a login page.
        if url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            target = f"{target}?{urlencode({'next': next_url})}"
    return HttpResponseRedirect(target)


class RateLimitedCallbackView(OIDCAuthenticationCallbackView):
    """The OIDC callback, with a per-address cap on attempts.

    The callback performs a token exchange and a userinfo fetch against
    Keycloak for every request, so an unauthenticated caller can otherwise use
    it to drive load onto the identity provider.

    The counter lives in the Django cache. Under the default local-memory
    cache that means the limit is per worker process rather than per site,
    which is a weaker guarantee than it looks - point ``CACHES['default']`` at
    a shared backend if that matters.
    """

    #: attempts allowed per client address within the window
    rate = 20
    window_seconds = 300

    def get(self, request):
        key = f"qgis-sso-callback:{self._client_ip(request)}"
        try:
            # add() only succeeds on the first call, which is what starts the
            # window; incr() then counts within it.
            cache.add(key, 0, self.window_seconds)
            attempts = cache.incr(key)
        except Exception:  # pragma: no cover - a cache outage must not lock people out
            logger.exception("Rate limit cache unavailable; allowing the callback")
            attempts = 0

        if attempts > self.rate:
            logger.warning(
                "Rate limiting OIDC callback for %s", self._client_ip(request)
            )
            return render(
                request,
                "qgis_sso/sign_in_failed.html",
                {
                    "no_account": False,
                    "rate_limited": True,
                    "support_url": getattr(settings, "SSO_SUPPORT_URL", ""),
                },
                status=429,
            )
        return super().get(request)

    @staticmethod
    def _client_ip(request):
        # REMOTE_ADDR only. X-Forwarded-For is attacker-controlled unless the
        # proxy is known to overwrite it, and trusting it here would let one
        # caller spread its attempts across unlimited synthetic addresses.
        return request.META.get("REMOTE_ADDR", "unknown")
