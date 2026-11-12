# coding=utf-8
"""Views for the SSO sign-in journey."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render, resolve_url
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView

from . import tiers
from .auth import REFUSAL_SESSION_KEY

logger = logging.getLogger(__name__)


def failure_page(request: HttpRequest, status: int, **reason: bool) -> HttpResponse:
    """Render the sign-in failure page, with the flag that names the reason."""
    context = {"support_url": getattr(settings, "SSO_SUPPORT_URL", ""), **reason}
    return render(request, "qgis_sso/sign_in_failed.html", context, status=status)


class HelpView(TemplateView):
    """The contributor's guide to signing in, open to anybody.

    The role table comes from the settings that define the roles, so it stays
    current.
    """

    template_name = "qgis_sso/help.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["roles"] = [
            {"name": tiers.label(role), "summary": tiers.summary(role)}
            for role in getattr(settings, "SSO_ROLE_LABELS", {})
        ]
        return context


def sign_in_failed(request: HttpRequest) -> HttpResponse:
    """Explain a failed sign-in.

    ``mozilla-django-oidc`` sends every failure here. The usual cause is a realm
    user with no account on this site, so the backend flags that case.
    """
    reason = request.session.pop(REFUSAL_SESSION_KEY, None)
    return failure_page(request, 403, no_account=reason == "no-account")


def site_login(request: HttpRequest, *args, **kwargs) -> HttpResponse:
    """The login page, without the loop for somebody already signed in.

    ``permission_required`` sends a signed-in user who lacks a permission here
    with a ``next``. We explain that instead of showing the form again. With
    no ``next``, we send them on to the site.

    The session is checked rather than ``request.user``, because
    ``QgisFeedUserVisitMiddleware`` signs anonymous visitors in as ``qgis_user``.
    """
    if not request.session.get(SESSION_KEY):
        return LoginView.as_view()(request, *args, **kwargs)
    if request.GET.get("next"):
        return failure_page(request, 403, no_permission=True)
    return HttpResponseRedirect(resolve_url(settings.LOGIN_REDIRECT_URL))


def admin_login(request: HttpRequest) -> HttpResponse:
    """Send Django admin's login form to the site login page.

    The admin form accepts every backend, so it would keep taking local
    passwords after ``LOCAL_LOGIN_ENABLED`` is switched off.
    """
    target = reverse("login")
    next_url = request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = f"{target}?{urlencode({'next': next_url})}"
    return HttpResponseRedirect(target)


class RateLimitedCallbackView(OIDCAuthenticationCallbackView):
    """The OIDC callback, limited per client address.

    Each callback costs a token exchange and a userinfo call to Keycloak. The
    counter lives in the cache, which is per worker process by default.
    """

    rate = 20
    window_seconds = 300

    def get(self, request: HttpRequest) -> HttpResponse:
        # REMOTE_ADDR only, because a client can forge X-Forwarded-For.
        address = request.META.get("REMOTE_ADDR", "unknown")
        key = f"qgis-sso-callback:{address}"
        try:
            cache.add(key, 0, self.window_seconds)
            attempts = cache.incr(key)
        except Exception:  # pragma: no cover - a cache outage must not lock people out
            logger.exception("Rate limit cache unavailable; allowing the callback")
            attempts = 0

        if attempts > self.rate:
            logger.warning("Rate limiting OIDC callback for %s", address)
            return failure_page(request, 429, rate_limited=True)
        return super().get(request)
