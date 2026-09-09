# coding=utf-8
"""The enrolment pages under ``/sso/manage/``.

Two of them, because they are two jobs. The list is about accounts that already
exist in the realm and where each of them has got to; the create page is about
accounts that do not exist yet. Holding both in one table meant a filter had to
be set before either question could be answered.

The list acts on one row at a time. A checkbox selection cannot survive paging,
and a wave is what the Django admin's bulk actions are for; all of them call the
same engine in :mod:`qgis_sso.actions`, so there is one set of rules.

Nothing here decides anything. Deciding is :mod:`qgis_sso.provisioning`'s job,
state is :mod:`qgis_sso.enrolment`'s, and this module renders and reports.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View

from .actions import (
    is_sso_administrator,
    issue_setup_links,
    max_users_per_action,
    plan_provisioning,
    run_provisioning,
    send_setup_emails,
)
from .enrolment import LINKED_STATES, STATES, candidate_rows, linked_rows
from .keycloak import KeycloakError
from .models import KeycloakIdentity
from .provisioning import Provisioner, setup_redirect_uri

SEND_EMAIL = "send-email"
ISSUE_LINK = "issue-link"

#: Rows a page on the list. Deliberately not the selection cap on the create
#: page: that exists because each account costs several synchronous calls to
#: Keycloak, which has nothing to do with how much fits on a screen.
PER_PAGE = 10

superuser_only = method_decorator(
    user_passes_test(is_sso_administrator), name="dispatch"
)


def report(request, run):
    """Say what happened to each account, by name.

    Shared by both pages so one action never reads differently from another.
    """
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


@superuser_only
class EnrolmentView(View):
    """Accounts that exist in the realm, one row at a time.

    Superuser-only, not merely staff: the service account behind these buttons
    can create realm users and grant client roles in a realm shared with hub and
    plugins. A user who fails that test is sent to the login page with a
    ``next``, which renders the "your account lacks the permission" page rather
    than a login form they are already past.
    """

    template_name = "qgis_sso/manage/enrolment.html"

    def get(self, request):
        return render(request, self.template_name, self.page_context(request))

    def post(self, request):
        action = request.POST.get("action", "")
        identity = self.selected_identity(request)
        context = self.page_context(request)

        if identity is None:
            messages.error(request, _("That account is not in the realm."))
            return render(request, self.template_name, context)

        if action == ISSUE_LINK:
            return self.issue_link(request, identity, context)
        if action == SEND_EMAIL:
            return self.send_email(request, identity, context)

        messages.error(request, _("Unknown action."))
        return render(request, self.template_name, context)

    @staticmethod
    def selected_identity(request):
        """The identity this row acts on, or None.

        Resolved from the database rather than trusted: a hand-made POST can
        carry anything, including the primary key of an account that has no
        realm identity at all.
        """
        value = request.POST.get("identity", "")
        if not value.isdigit():
            return None
        return KeycloakIdentity.objects.filter(pk=value).select_related("user").first()

    # -- the actions -------------------------------------------------------

    def send_email(self, request, identity, context):
        """Email one person their setup link, after confirming.

        Confirmed because it reaches a real contributor and cannot be unsent;
        the staging database holds every one of their real addresses.
        """
        if request.POST.get("confirm") != "yes":
            context.update({"confirming": SEND_EMAIL, "subject": identity})
            return render(request, self.template_name, context)

        session = self.session(request)
        if session is None:
            return render(request, self.template_name, context)
        provisioner, redirect_uri = session

        run = send_setup_emails(provisioner, [identity], redirect_uri)
        report(request, run)
        return self.back(request)

    def issue_link(self, request, identity, context):
        """Fetch one sign-in link and show it once.

        Not confirmed: nothing leaves this building until an administrator
        passes the link on. Not redirected either, and this is the only action
        that is not - the link exists on this one response and nowhere else. It
        is a bearer credential, so it is never stored, never logged, and never
        put through the messages framework, whose fallback storage is a cookie.
        """
        session = self.session(request)
        if session is None:
            return render(request, self.template_name, context)
        provisioner, redirect_uri = session

        try:
            run = issue_setup_links(provisioner, [identity], redirect_uri)
        except KeycloakError as error:
            messages.error(request, str(error))
            return render(request, self.template_name, context)

        report(request, run)
        fresh = self.page_context(request)
        issued = [outcome for outcome in run.succeeded if outcome.link]
        if issued:
            fresh["issued"] = issued[0]
        response = render(request, self.template_name, fresh)
        response["Cache-Control"] = "no-store"
        return response

    def session(self, request):
        """``(provisioner, redirect_uri)``, or None with the reason reported."""
        try:
            redirect_uri = setup_redirect_uri()
            return Provisioner(), redirect_uri
        except ValueError as error:
            messages.error(request, str(error))
        except KeycloakError as error:
            messages.error(request, _("Could not reach Keycloak: %s") % error)
        return None

    # -- rendering ---------------------------------------------------------

    def back(self, request):
        """Redirect to the list, keeping the filters, after something happened.

        A finished action must not be replayable by a browser refresh. The
        messages survive the redirect; the POST does not.
        """
        query = {
            key: request.GET[key]
            for key in ("state", "q", "page")
            if request.GET.get(key)
        }
        url = reverse("qgis_sso:enrolment")
        if query:
            url = f"{url}?{urlencode(query)}"
        return HttpResponseRedirect(url)

    def page_context(self, request):
        state = request.GET.get("state", "")
        search = request.GET.get("q", "").strip()

        matching = linked_rows()
        counts = {key: 0 for key in LINKED_STATES}
        for row in matching:
            counts[row.state] += 1

        if search:
            needle = search.lower()
            matching = [
                row
                for row in matching
                if needle in row.user.username.lower()
                or needle in (row.user.email or "").lower()
            ]
        if state in STATES:
            matching = [row for row in matching if row.state == state]

        paginator = Paginator(matching, PER_PAGE)
        try:
            page = paginator.page(request.GET.get("page", 1))
        except PageNotAnInteger:
            page = paginator.page(1)
        except EmptyPage:
            page = paginator.page(paginator.num_pages)

        return {
            "page": page,
            "rows": page.object_list,
            "state": state,
            "search": search,
            "totals": [(key, STATES[key][0], counts[key]) for key in LINKED_STATES],
        }


@superuser_only
class CreateAccountsView(View):
    """Accounts that have no realm identity yet.

    Not paginated, and that is the point: this is the one place a selection is
    made, and a selection cannot be lost by paging if there is no paging. The
    search box is how a long list is narrowed.
    """

    template_name = "qgis_sso/manage/create.html"

    def get(self, request):
        return render(request, self.template_name, self.page_context(request))

    def post(self, request):
        context = self.page_context(request)
        # Only digits reach the query: a hand-made POST can put anything here,
        # and a non-numeric primary key raises rather than matching nothing.
        selected = [
            value for value in request.POST.getlist("selected") if value.isdigit()
        ]
        # Resolved against the candidates rather than the user table, so an
        # account this page excludes cannot be provisioned by posting its id.
        candidates, _hidden = candidate_rows()
        allowed = {str(row.user.pk): row.user for row in candidates}
        users = [allowed[pk] for pk in selected if pk in allowed]
        refused = len(selected) - len(users)

        if refused:
            messages.error(
                request,
                _(
                    "%d of the selected accounts are not candidates - they have "
                    "one already, or no invitation could reach them - and were "
                    "left alone."
                )
                % refused,
            )
        if not users:
            if not refused:
                messages.warning(request, _("Nothing was selected."))
            return render(request, self.template_name, context)

        limit = max_users_per_action()
        if len(users) > limit:
            messages.error(
                request,
                _(
                    "Selected %(count)d accounts, more than the limit of "
                    "%(limit)d for one run. Work in smaller waves: each account "
                    "costs several calls to Keycloak and the request would time "
                    "out part-way through."
                )
                % {"count": len(users), "limit": limit},
            )
            return render(request, self.template_name, context)

        include_flagged = request.POST.get("include_flagged") == "yes"
        try:
            provisioner, decisions = plan_provisioning(users, include_flagged)
        except KeycloakError as error:
            messages.error(request, _("Could not reach Keycloak: %s") % error)
            return render(request, self.template_name, context)

        if request.POST.get("confirm") != "yes":
            context.update(
                {
                    "confirming": True,
                    "decisions": decisions,
                    "selected": [str(user.pk) for user in users],
                    "include_flagged": include_flagged,
                }
            )
            return render(request, self.template_name, context)

        run = run_provisioning(provisioner, decisions)
        report(request, run)
        if run.succeeded:
            messages.info(
                request,
                _(
                    "Nobody has been emailed. Inviting is a separate step on the "
                    "enrolment list, so it is never a side effect of creating an "
                    "account."
                ),
            )
        return HttpResponseRedirect(reverse("qgis_sso:create"))

    def page_context(self, request):
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
