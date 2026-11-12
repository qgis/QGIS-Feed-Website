# coding=utf-8
"""The people pages under ``/sso/manage/``.

The list shows accounts that already exist in the realm and acts on one row at
a time. A superuser sees every account, and everybody else sees the accounts
they vouched for. The invite pages create the accounts that do not exist yet.
Deciding is :mod:`qgis_sso.provisioning`'s job and state is
:mod:`qgis_sso.enrolment`'s, so this module renders and reports.
"""

import logging
from urllib.parse import urlencode

import segno
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
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

from . import provisioning, validation
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
from .models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from .provisioning import Provisioner, setup_redirect_uri
from .tiers import invitable_roles
from .tiers import label as role_label
from .tiers import may_invite, remaining
from .views_profile import signed_in

logger = logging.getLogger(__name__)

#: The one row action that acts at once. Emailing and cancelling ask first.
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
    "sponsor": (Lower("sponsor_name"), "asc"),
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
    """How to order the list.

    Accounts with no sponsor stay at the far end either way, and the primary
    key breaks ties so that pages never overlap.
    """
    column = SORT_KEYS[sort_by][0]
    if order == "desc":
        return [column.desc(nulls_last=True), "-pk"]
    return [column.asc(nulls_last=True), "pk"]


superuser_only = method_decorator(
    user_passes_test(is_sso_administrator), name="dispatch"
)


class SignedInMixin:
    """Send anybody who is not signed in to the login page first."""

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not signed_in(request):
            return HttpResponseRedirect(f"{reverse('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)


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


def actionable_identity(user: User, value: object) -> KeycloakIdentity | None:
    """The identity this person may act on, looked up rather than trusted.

    Anybody but a superuser only reaches the accounts they vouched for, so a
    typed id cannot reach somebody else's.
    """
    value = str(value)
    if not value.isdigit():
        return None
    identities = KeycloakIdentity.objects.select_related("user")
    if not user.is_superuser:
        identities = identities.filter(sponsor=user)
    return identities.filter(pk=value).first()


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


class EnrolmentView(SignedInMixin, View):
    """Accounts that exist in the realm, one row at a time.

    The same scoping applies when a row is acted on, because a list that only
    hides a row does not stop anybody posting its id.
    """

    template_name = "qgis_sso/manage/enrolment.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self.page_context(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        identity = actionable_identity(request.user, request.POST.get("identity", ""))
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

        It needs no confirmation, because nothing reaches the person until
        somebody passes the link on. The link is a bearer credential, so it is
        never stored or logged, and it reaches the next page through the
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
        """The column and direction from the query string, or the defaults.

        Only a superuser sees the sponsor column, so only they can sort by it.
        """
        sort_by = request.GET.get("sort_by", "")
        if sort_by not in SORT_KEYS:
            sort_by = DEFAULT_SORT
        if sort_by == "sponsor" and not request.user.is_superuser:
            sort_by = DEFAULT_SORT
        order = request.GET.get("order", "")
        if order not in ("asc", "desc"):
            order = SORT_KEYS[sort_by][1]
        return sort_by, order

    def page_context(self, request: HttpRequest) -> dict[str, object]:
        state = request.GET.get("state", "")
        search = request.GET.get("q", "").strip()

        # A queryset up to the paginator, so a page of ten costs a page of ten.
        matching = linked_identities(
            sponsored_by=None if request.user.is_superuser else request.user,
        )

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
        matching = matching.order_by(*ordering(sort_by, order)).select_related(
            "user", "sponsor"
        )
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
            "may_invite_new": bool(invitable_roles(request.user)),
            "everyone": request.user.is_superuser,
            "sort_by": sort_by,
            "order": order,
            "filters": filter_query(request),
        }


class IssuedLinkView(SignedInMixin, View):
    """One freshly issued setup link, shown once with a QR code of it.

    The row action and the invite form both end here. The link leaves the
    session as the page renders, so a reload shows nothing. The QR code is
    drawn in this process as inline SVG.
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


class RowConfirmView(SignedInMixin, View):
    """One action on one row of the people list, on a page of its own.

    It applies the list's rule for the row again, because a typed URL has no
    row to hide it.
    """

    template_name = ""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        identity = actionable_identity(request.user, pk)
        refused = self.refusal_for(request, identity)
        if refused:
            return refused
        return render(request, self.template_name, self.context(request, identity))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        identity = actionable_identity(request.user, pk)
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


class CancelInvitationView(RowConfirmView):
    """Undo an invitation, which deletes the account and frees the place.

    Only an invitation made on this site, and only before anybody has used it.
    :func:`provisioning.cancel_invitation` does the work.
    """

    template_name = "qgis_sso/manage/cancel_invitation.html"

    def blocked(self, identity: KeycloakIdentity) -> str | None:
        if identity.has_logged_in_via_sso:
            return _(
                "%(username)s signed in already, so there is no invitation "
                "left to cancel."
            ) % {"username": identity.user.username}
        if identity.sponsor_id is None or identity.link_method != LinkMethod.INVITATION:
            return _(
                "%(username)s did not arrive on an invitation from this page, so "
                "there is no invitation to cancel."
            ) % {"username": identity.user.username}
        return None

    def act(self, request: HttpRequest, identity: KeycloakIdentity) -> HttpResponse:
        username = identity.user.username
        try:
            provisioning.cancel_invitation(identity, request.user)
        except provisioning.CancelError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(list_url(request))

        messages.success(
            request,
            _("The invitation for %(username)s is cancelled and the account is gone.")
            % {"username": username},
        )
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

        run = run_provisioning(provisioner, decisions, sponsor=request.user)
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


class InviteNewView(SignedInMixin, View):
    """Bring in somebody who has no account here at all.

    Open to anybody whose roles carry a quota. The inviter types the address,
    and the account is made at once, so no public endpoint ever makes accounts.
    """

    template_name = "qgis_sso/manage/invite_new.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if signed_in(request) and not invitable_roles(request.user):
            messages.error(request, _("Your account cannot invite anybody."))
            return HttpResponseRedirect(reverse("qgis_sso:enrolment"))
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self.page_context(request, {}))

    def post(self, request: HttpRequest) -> HttpResponse:
        form = {
            key: request.POST.get(key, "").strip()
            for key in ("username", "email", "first_name", "last_name", "role")
        }
        # Cleaned first, so every check and the new account see one name.
        form["username"] = validation.clean_username(form["username"])

        error = self.check_locally(request.user, form)
        if error:
            return self.again(request, form, error)

        try:
            provisioner = Provisioner()
            taken = self.check_realm(provisioner, form)
            if taken:
                return self.again(request, form, taken)
            identity = self.create(request, provisioner, form)
        except KeycloakError as error:
            logger.warning("Invitation failed against the realm: %s", error)
            return self.again(
                request,
                form,
                _("The account could not be created just now. Try again shortly."),
            )

        messages.success(
            request,
            _("%(username)s now has an account. Nothing has been sent to them yet.")
            % {"username": identity.user.username},
        )
        # The realm cannot always mint a link, and the account exists either way.
        if ISSUED_SESSION_KEY in request.session:
            return HttpResponseRedirect(reverse("qgis_sso:issued_link"))
        return HttpResponseRedirect(reverse("qgis_sso:enrolment"))

    def check_locally(self, user: User, form: dict[str, str]) -> str | None:
        """Whatever is wrong with the form, one message at a time."""
        if not form["role"]:
            return _("Choose a role for them.")
        if not may_invite(user, form["role"]):
            # One message for an unknown role and one above their tier.
            return _("You cannot invite somebody at that level.")

        left = remaining(user)
        if left is not None and left <= 0:
            return _(
                "Every place you have is waiting for somebody. A place comes "
                "back when one of them signs in, or when you cancel an "
                "invitation on the people page."
            )

        if not form["username"]:
            return _("Choose a username.")
        if not form["email"]:
            return _("An email address is needed to send the setup link.")

        # What the realm would refuse, said here instead of there.
        for problem in (
            validation.username_error(form["username"]),
            validation.email_error(form["email"]),
            validation.person_name_error(form["first_name"], _("first name")),
            validation.person_name_error(form["last_name"], _("last name")),
        ):
            if problem:
                return problem

        if User.objects.filter(username__iexact=form["username"]).exists():
            return _("That username is taken here. Choose another.")
        if User.objects.filter(email__iexact=form["email"]).exists():
            return _("There is already an account here for that address.")
        return None

    @staticmethod
    def check_realm(provisioner: Provisioner, form: dict[str, str]) -> str | None:
        """Whether the realm already knows this username or address.

        An address in the realm belongs to somebody, who would otherwise get a
        setup link they never asked for.
        """
        if provisioner.client.find_user_by_username(form["username"]):
            return _("That name is taken on another QGIS site. Choose another.")
        if provisioner.client.find_user_by_email(form["email"]):
            return _(
                "That address already has a QGIS account. Invite them as an "
                "existing user instead, or ask them to sign in."
            )
        return None

    def create(
        self, request: HttpRequest, provisioner: Provisioner, form: dict[str, str]
    ) -> KeycloakIdentity:
        """Make the account here and in the realm, then fetch its setup link.

        The realm write cannot roll back with the transaction. A failure after
        it leaves an unused realm account, which is easier to clean up than a
        local account with nothing behind it.
        """
        with transaction.atomic():
            user = User.objects.create_user(
                username=form["username"],
                email=form["email"],
                first_name=form["first_name"],
                last_name=form["last_name"],
            )
            # This account signs in through Keycloak or not at all.
            user.set_unusable_password()
            user.save(update_fields=["password"])

            decision = provisioner.inspect(user, roles=[form["role"]])
            if not decision.actionable:
                raise KeycloakError(decision.reason)

            # The sponsor goes in with the insert, where the check constraint fires.
            identity = provisioner.provision(
                decision,
                sponsor=request.user,
                link_method=LinkMethod.INVITATION,
            )
            SsoAuditEvent.record(
                SsoAuditEvent.Action.INVITED,
                user=user,
                sub=identity.sub,
                sponsor=request.user.username,
                role=form["role"],
            )

        # After the commit, so a link never outlives a rolled back account.
        self.offer_link(request, provisioner, identity)
        return identity

    @staticmethod
    def offer_link(
        request: HttpRequest, provisioner: Provisioner, identity: KeycloakIdentity
    ) -> None:
        """Fetch the setup link if the realm can mint one. Failing is fine."""
        try:
            link = provisioner.issue_setup_link(identity, setup_redirect_uri())
        except (KeycloakError, ValueError) as error:
            logger.info("No setup link issued for %s: %s", identity.sub, error)
            return
        request.session[ISSUED_SESSION_KEY] = {
            "username": identity.user.username,
            "link": link,
        }

    def again(
        self, request: HttpRequest, form: dict[str, str], error: str
    ) -> HttpResponse:
        context = self.page_context(request, form)
        context["error"] = error
        return render(request, self.template_name, context, status=400)

    @staticmethod
    def page_context(request: HttpRequest, form: dict[str, str]) -> dict[str, object]:
        left = remaining(request.user)
        return {
            "form": form,
            # The form posts the Keycloak role name, and the reader sees the label.
            "roles": [
                (role, role_label(role)) for role in invitable_roles(request.user)
            ],
            "remaining": left,
            "unlimited": left is None,
        }
