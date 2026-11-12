# coding=utf-8
"""The people pages under ``/sso/manage/``, for administrators.

The list shows accounts that already exist in the realm and acts on one row at
a time. The invite page creates the accounts that do not exist yet. Deciding is
:mod:`qgis_sso.provisioning`'s job and state is :mod:`qgis_sso.enrolment`'s, so
this module renders and reports.
"""

import logging
from urllib.parse import urlencode

import segno
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, F, Q
from django.db.models.functions import Lower
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from django.views import View

from .actions import (
    Run,
    is_sso_administrator,
    issue_setup_links,
    max_users_per_action,
    plan_provisioning,
    run_provisioning,
    send_setup_emails,
)
from .enrolment import (
    LINKED_STATES,
    STATE_ORDER,
    STATES,
    candidate_rows,
    linked_identities,
    rows_for,
)
from .keycloak import KeycloakError
from .models import KeycloakIdentity
from .provisioning import Provisioner, setup_redirect_uri

logger = logging.getLogger(__name__)

#: The one row action that acts at once. Emailing asks first, on its own page.
ISSUE_LINK = "issue-link"

#: Where a freshly issued setup link waits for the page that shows it once.
#: Never the messages framework, whose fallback storage is a cookie.
ISSUED_SESSION_KEY = "qgis_sso_issued_link"

PER_PAGE = 10

#: The columns the list may be sorted by, and the direction of a first click.
SORT_KEYS = {
    "account": (Lower("user__username"), "asc"),
    "state": (STATE_ORDER, "asc"),
    "invited": (F("linked_at"), "desc"),
}

DEFAULT_SORT = "invited"

#: The query keys the list reads, carried to a confirmation page and back.
FILTER_KEYS = ("state", "q", "page", "sort_by", "order")

#: Translated where they are shown.
UNREACHABLE = gettext_noop(
    "We could not reach the QGIS account service. Try again in a moment."
)
NOT_FOUND = gettext_noop(
    "We could not find that account. Reload the list and try again."
)


def ordering(sort_by: str, order: str) -> list:
    """How to order the list, ending on the primary key so pages never overlap."""
    column = SORT_KEYS[sort_by][0]
    if order == "desc":
        return [column.desc(nulls_last=True), "-pk"]
    return [column.asc(nulls_last=True), "pk"]


superuser_only = method_decorator(
    user_passes_test(is_sso_administrator), name="dispatch"
)


def report(request: HttpRequest, run: Run) -> None:
    """Say what happened to each account, by name."""
    for outcome in run.outcomes:
        text = _("%(username)s: %(message)s") % {
            "username": outcome.username,
            "message": outcome.message,
        }
        if outcome.ok:
            messages.success(request, text)
        elif outcome.failed:
            messages.error(request, text)
        else:
            messages.warning(request, text)


def filter_query(request: HttpRequest) -> str:
    """The list's filters, as a query string, or empty."""
    return urlencode(
        {key: request.GET[key] for key in FILTER_KEYS if request.GET.get(key)}
    )


def list_url(request: HttpRequest) -> str:
    """The people list, showing whatever it was showing before."""
    url = reverse("qgis_sso:enrolment")
    query = filter_query(request)
    return f"{url}?{query}" if query else url


def actionable_identity(value: object) -> KeycloakIdentity | None:
    """The identity a row action is about, looked up rather than trusted."""
    value = str(value)
    if not value.isdigit():
        return None
    return KeycloakIdentity.objects.select_related("user").filter(pk=value).first()


def provisioner_session(request: HttpRequest) -> tuple[Provisioner, str] | None:
    """``(provisioner, redirect_uri)``, or None with the reason reported."""
    try:
        redirect_uri = setup_redirect_uri()
        return Provisioner(), redirect_uri
    except ValueError as error:
        messages.error(request, str(error))
    except KeycloakError:
        logger.exception("Could not reach Keycloak for the people pages")
        messages.error(request, _(UNREACHABLE))
    return None


def already_signed_in(identity: KeycloakIdentity) -> str:
    return _(
        "%(username)s has already signed in, so there is nothing to set up. "
        "They manage their passkeys from their own account."
    ) % {"username": identity.user.username}


@superuser_only
class EnrolmentView(View):
    """Accounts that exist in the realm, one row at a time."""

    template_name = "qgis_sso/manage/enrolment.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self.page_context(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        identity = actionable_identity(request.POST.get("identity", ""))
        if identity is None:
            messages.error(request, _(NOT_FOUND))
            return self.back(request)

        if request.POST.get("action", "") != ISSUE_LINK:
            messages.error(request, _("That action is not available here."))
            return self.back(request)

        # The row hides the button too, but a hand-made POST has no row.
        if identity.has_logged_in_via_sso:
            messages.error(request, already_signed_in(identity))
            return self.back(request)

        return self.issue_link(request, identity)

    def issue_link(
        self, request: HttpRequest, identity: KeycloakIdentity
    ) -> HttpResponse:
        """Fetch one setup link and hand it to the page that shows it.

        It needs no confirmation, because nothing reaches the person until an
        administrator passes the link on. The link is a bearer credential, so
        it is never stored or logged, and it reaches the next page through the
        server-side session.
        """
        session = provisioner_session(request)
        if session is None:
            return self.back(request)
        provisioner, redirect_uri = session

        run = issue_setup_links(provisioner, [identity], redirect_uri)
        issued = [outcome for outcome in run.succeeded if outcome.link]
        if not issued:
            report(request, run)
            return self.back(request)

        request.session[ISSUED_SESSION_KEY] = {
            "username": issued[0].username,
            "link": issued[0].link,
        }
        return HttpResponseRedirect(reverse("qgis_sso:issued_link"))

    @staticmethod
    def back(request: HttpRequest) -> HttpResponse:
        """Back to the list, so a refresh cannot replay the POST."""
        return HttpResponseRedirect(list_url(request))

    @staticmethod
    def sorting(request: HttpRequest) -> tuple[str, str]:
        """The column and direction from the query string, or the defaults."""
        sort_by = request.GET.get("sort_by", "")
        if sort_by not in SORT_KEYS:
            sort_by = DEFAULT_SORT
        order = request.GET.get("order", "")
        if order not in ("asc", "desc"):
            order = SORT_KEYS[sort_by][1]
        return sort_by, order

    def page_context(self, request: HttpRequest) -> dict[str, object]:
        state = request.GET.get("state", "")
        search = request.GET.get("q", "").strip()

        # A queryset up to the paginator, so a page of ten costs a page of ten.
        matching = linked_identities()

        # Counted before filtering, so each state shows its full total. The
        # empty order_by() keeps the model ordering out of the grouping.
        counted = dict(
            matching.order_by().values_list("state").annotate(total=Count("pk"))
        )
        counts = {key: counted.get(key, 0) for key in LINKED_STATES}

        if search:
            matching = matching.filter(
                Q(user__username__icontains=search) | Q(user__email__icontains=search)
            )
        if state in STATES:
            matching = matching.filter(state=state)

        sort_by, order = self.sorting(request)
        matching = matching.order_by(*ordering(sort_by, order)).select_related("user")
        # proposed_roles reads the groups.
        matching = matching.prefetch_related("user__groups")

        paginator = Paginator(matching, PER_PAGE)
        try:
            page = paginator.page(request.GET.get("page", 1))
        except PageNotAnInteger:
            page = paginator.page(1)
        except EmptyPage:
            page = paginator.page(paginator.num_pages)

        return {
            "page": page,
            "rows": rows_for(page.object_list),
            "state": state,
            "search": search,
            "totals": [(key, STATES[key][0], counts[key]) for key in LINKED_STATES],
            "sort_by": sort_by,
            "order": order,
            "filters": filter_query(request),
        }


@superuser_only
class IssuedLinkView(View):
    """One freshly issued setup link, shown once with a QR code of it.

    The link leaves the session as the page renders, so a reload shows nothing.
    The QR code is drawn in this process as inline SVG.
    """

    template_name = "qgis_sso/manage/issued_link.html"

    #: ``omitsize`` gives the SVG a viewBox, so the stylesheet can scale it
    #: without cropping. A cropped QR code does not scan.
    QR_SVG = {"scale": 5, "omitsize": True, "svgclass": None, "lineclass": None}

    def get(self, request: HttpRequest) -> HttpResponse:
        issued = request.session.pop(ISSUED_SESSION_KEY, None)
        context = {"issued": issued, "qr": self.qr(issued)}
        response = render(request, self.template_name, context)
        response["Cache-Control"] = "no-store"
        return response

    @classmethod
    def qr(cls, issued: dict | None) -> SafeString | None:
        """The link as inline SVG, or None.

        Safe to mark, because segno draws geometry and never copies the text.
        """
        if not issued:
            return None
        code = segno.make(issued["link"], error="m")
        return mark_safe(  # noqa: S308 - geometry only, see the docstring
            code.svg_inline(**cls.QR_SVG)
        )


@superuser_only
class RowConfirmView(View):
    """One action on one row of the people list, on a page of its own.

    It applies the list's rule for the row again, because a typed URL has no
    row to hide it.
    """

    template_name = ""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        identity = actionable_identity(pk)
        refused = self.refusal_for(request, identity)
        if refused:
            return refused
        return render(request, self.template_name, self.context(request, identity))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        identity = actionable_identity(pk)
        refused = self.refusal_for(request, identity)
        if refused:
            return refused
        return self.act(request, identity)

    def refusal_for(
        self, request: HttpRequest, identity: KeycloakIdentity | None
    ) -> HttpResponse | None:
        """A redirect saying why this is refused, or None to carry on."""
        if identity is None:
            return self.refuse(request, _(NOT_FOUND))
        blocked = self.blocked(identity)
        if blocked:
            return self.refuse(request, blocked)
        return None

    def blocked(self, identity: KeycloakIdentity) -> str | None:
        """Why this action cannot happen to this account, or None."""
        return None

    def act(self, request: HttpRequest, identity: KeycloakIdentity) -> HttpResponse:
        raise NotImplementedError

    @staticmethod
    def context(request: HttpRequest, identity: KeycloakIdentity) -> dict[str, object]:
        return {"subject": identity, "back": list_url(request)}

    @staticmethod
    def refuse(request: HttpRequest, message: str) -> HttpResponse:
        messages.error(request, message)
        return HttpResponseRedirect(list_url(request))


class SendEmailView(RowConfirmView):
    """Emailing one person their setup link, which cannot be unsent."""

    template_name = "qgis_sso/manage/send_email.html"

    def blocked(self, identity: KeycloakIdentity) -> str | None:
        if identity.has_logged_in_via_sso:
            return already_signed_in(identity)
        return None

    def act(self, request: HttpRequest, identity: KeycloakIdentity) -> HttpResponse:
        session = provisioner_session(request)
        if session is None:
            return HttpResponseRedirect(list_url(request))
        provisioner, redirect_uri = session

        run = send_setup_emails(provisioner, [identity], redirect_uri)
        report(request, run)
        return HttpResponseRedirect(list_url(request))


@superuser_only
class InviteExistingView(View):
    """Accounts that have no realm identity yet.

    It has no paging, so a selection cannot be lost to it. The search box
    narrows a long list.
    """

    template_name = "qgis_sso/manage/invite_existing.html"

    #: The first post renders this preview and the second one acts. It cannot
    #: be a GET, because the selection lives in the form.
    confirm_template_name = "qgis_sso/manage/invite_existing_confirm.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self.page_context(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        # A non-numeric primary key would raise rather than match nothing.
        selected = [
            value for value in request.POST.getlist("selected") if value.isdigit()
        ]
        # Checked against the candidates, so an excluded account cannot be posted.
        candidates, _hidden = candidate_rows()
        allowed = {str(row.user.pk): row.user for row in candidates}
        users = [allowed[pk] for pk in selected if pk in allowed]
        refused = len(selected) - len(users)

        if refused:
            messages.error(
                request,
                _(
                    "%d of the selected accounts are not candidates, so we left "
                    "them alone. They already have a QGIS account, or no "
                    "invitation could reach them."
                )
                % refused,
            )
        if not users:
            if not refused:
                messages.warning(request, _("Nothing was selected."))
            return self.render_list(request)

        limit = max_users_per_action()
        if len(users) > limit:
            messages.error(
                request,
                _(
                    "You selected %(count)d accounts, but one run takes at most "
                    "%(limit)d. Each account costs several calls to Keycloak, "
                    "so work in smaller waves."
                )
                % {"count": len(users), "limit": limit},
            )
            return self.render_list(request)

        include_flagged = request.POST.get("include_flagged") == "yes"
        try:
            provisioner, decisions = plan_provisioning(users, include_flagged)
        except KeycloakError:
            logger.exception("Could not reach Keycloak to plan an invitation")
            messages.error(request, _(UNREACHABLE))
            return self.render_list(request)

        if request.POST.get("confirm") != "yes":
            return render(
                request,
                self.confirm_template_name,
                {
                    "decisions": decisions,
                    "selected": [str(user.pk) for user in users],
                    "include_flagged": include_flagged,
                },
            )

        run = run_provisioning(provisioner, decisions)
        report(request, run)
        if run.succeeded:
            messages.info(
                request,
                _(
                    "Nobody has been emailed. Send the setup email from the "
                    "people page when they are ready."
                ),
            )
        return HttpResponseRedirect(reverse("qgis_sso:invite_existing"))

    def render_list(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self.page_context(request))

    def page_context(self, request: HttpRequest) -> dict[str, object]:
        search = request.GET.get("q", "").strip()

        candidates, hidden = candidate_rows()
        total = len(candidates)
        if search:
            needle = search.lower()
            candidates = [
                row
                for row in candidates
                if needle in row.user.username.lower()
                or needle in (row.user.email or "").lower()
            ]

        return {
            "rows": candidates,
            "search": search,
            "total": total,
            "hidden": hidden,
            "max_users": max_users_per_action(),
        }
