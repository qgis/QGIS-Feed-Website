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

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View

from . import revocation
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
from .models import KeycloakIdentity, LinkMethod, SsoAuditEvent
from .provisioning import Provisioner, setup_redirect_uri
from .tiers import invitable_roles, may_invite, remaining
from .views_profile import signed_in, trusted

logger = logging.getLogger(__name__)

SEND_EMAIL = "send-email"
ISSUE_LINK = "issue-link"
RESTORE = "restore"

#: Where a freshly issued setup link waits for the page it is shown on. It
#: cannot go through messages: that framework's fallback storage is a cookie,
#: and this is a credential. Popped by the listing, so it appears exactly once.
ISSUED_SESSION_KEY = "qgis_sso_issued_link"

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


class EnrolmentView(View):
    """Accounts that exist in the realm, one row at a time.

    Open to anybody with a realm account of their own, showing what each is
    entitled to see: a superuser gets every identity, everybody else only the
    accounts they vouched for. The scoping is applied again when a row is acted
    on, because a listing that merely omits a row does not stop anybody posting
    its id.
    """

    template_name = "qgis_sso/manage/enrolment.html"

    def dispatch(self, request, *args, **kwargs):
        if not signed_in(request):
            return HttpResponseRedirect(
                f"{reverse('login')}?next={reverse('qgis_sso:enrolment')}"
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, self.page_context(request))

    def post(self, request):
        action = request.POST.get("action", "")
        context = self.page_context(request)

        # A suspended account keeps is_active - US-5.2 says they may sign in -
        # so nothing else stops them acting on the people they invited.
        if not trusted(request.user):
            messages.error(request, _("Your account cannot make changes."))
            return render(request, self.template_name, context)

        identity = self.selected_identity(request)

        if identity is None:
            messages.error(request, _("That account is not in the realm."))
            return render(request, self.template_name, context)

        if action == ISSUE_LINK:
            return self.issue_link(request, identity, context)
        if action == SEND_EMAIL:
            return self.send_email(request, identity, context)
        if action == RESTORE:
            return self.restore(request, identity)

        messages.error(request, _("Unknown action."))
        return render(request, self.template_name, context)

    @staticmethod
    def selected_identity(request):
        """The identity this row acts on, or None.

        Resolved from the database rather than trusted, and narrowed to what
        this person may act on at all: a hand-made POST can carry anything,
        including the id of an account somebody else vouched for.
        """
        value = request.POST.get("identity", "")
        if not value.isdigit():
            return None
        identities = KeycloakIdentity.objects.select_related("user")
        if not request.user.is_superuser:
            identities = identities.filter(sponsor=request.user)
        return identities.filter(pk=value).first()

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

    def restore(self, request, identity):
        try:
            revocation.restore(request.user, identity)
        except revocation.RevocationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                _("%(username)s and their subtree are active again.")
                % {"username": identity.user.username},
            )
        return self.back(request)

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

        # A superuser sees the whole forest; everybody else sees their own
        # branch of it.
        matching = linked_rows(
            sponsored_by=None if request.user.is_superuser else request.user
        )
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
            "may_invite_new": bool(invitable_roles(request.user)),
            "everyone": request.user.is_superuser,
            # Shown once, then gone: put here by the invite form across its
            # redirect, because a credential cannot go through messages.
            "issued": request.session.pop(ISSUED_SESSION_KEY, None),
        }


@superuser_only
class InviteExistingView(View):
    """Accounts that have no realm identity yet.

    Not paginated, and that is the point: this is the one place a selection is
    made, and a selection cannot be lost by paging if there is no paging. The
    search box is how a long list is narrowed.
    """

    template_name = "qgis_sso/manage/invite_existing.html"

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

        run = run_provisioning(provisioner, decisions, sponsor=request.user)
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
        return HttpResponseRedirect(reverse("qgis_sso:invite_existing"))

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


class InviteNewView(View):
    """Bring in somebody who has no account here at all.

    The web-of-trust route, open to anyone whose roles carry quota. The account
    is created straight away rather than a link being handed out that creates
    one later: the inviter types the address, so nobody else can choose it, and
    there is no public endpoint that makes accounts.
    """

    template_name = "qgis_sso/manage/invite_new.html"

    def dispatch(self, request, *args, **kwargs):
        if not signed_in(request):
            return HttpResponseRedirect(
                f"{reverse('login')}?next={reverse('qgis_sso:invite_new')}"
            )
        if not trusted(request.user):
            messages.error(request, _("Your account cannot invite anybody."))
            return HttpResponseRedirect(reverse("qgis_sso:enrolment"))
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, self.page_context(request, {}))

    def post(self, request):
        form = {
            key: request.POST.get(key, "").strip()
            for key in ("username", "email", "first_name", "last_name", "role")
        }

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
            # Not echoed: it can carry realm detail the reader cannot act on.
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
        return HttpResponseRedirect(reverse("qgis_sso:enrolment"))

    # -- the checks --------------------------------------------------------

    def check_locally(self, user, form):
        """Whatever is wrong with the form, one message at a time."""
        if not may_invite(user, form["role"]):
            # One message for "no such role" and "above your tier": neither
            # tells a prober anything about the ladder.
            return _("You cannot invite somebody at that level.")

        left = remaining(user)
        if left is not None and left <= 0:
            return _(
                "You have no invitations left. They free up as the people you "
                "have already invited sign in."
            )

        if not form["username"]:
            return _("Choose a username.")
        if not form["email"]:
            return _("An email address is needed to send the setup link.")
        if User.objects.filter(username__iexact=form["username"]).exists():
            return _("That username is taken here. Choose another.")
        if User.objects.filter(email__iexact=form["email"]).exists():
            return _("There is already an account here for that address.")
        return None

    @staticmethod
    def check_realm(provisioner, form):
        """Whether the realm already knows this username or address.

        Checked before anything is written. An address already in the realm
        belongs to somebody, and enrolling a second account onto it would send
        them a setup link they never asked for.
        """
        if provisioner.client.find_user_by_username(form["username"].lower()):
            return _("That username is taken in the QGIS realm. Choose another.")
        if provisioner.client.find_user_by_email(form["email"]):
            return _(
                "That address already has a QGIS account. Invite them as an "
                "existing user instead, or ask them to sign in."
            )
        return None

    # -- the write ---------------------------------------------------------

    def create(self, request, provisioner, form):
        """Make the account here and in the realm, and fetch its setup link.

        One transaction. The realm write inside it cannot be rolled back, so a
        later failure leaves an unused realm account, which is recoverable by
        hand; the reverse - a local account with nothing behind it - is not.
        """
        with transaction.atomic():
            user = User.objects.create_user(
                username=form["username"],
                email=form["email"],
                first_name=form["first_name"],
                last_name=form["last_name"],
            )
            # No usable password, ever: this account signs in through Keycloak
            # or not at all.
            user.set_unusable_password()
            user.save(update_fields=["password"])

            decision = provisioner.inspect(user, roles=[form["role"]])
            if not decision.actionable:
                raise KeycloakError(decision.reason)

            # The sponsor goes in with the rest: the check constraint fires at
            # insert, so setting it afterwards is setting it too late.
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

        # After the commit: the link is worth nothing if the account it belongs
        # to has just been rolled back.
        self.offer_link(request, provisioner, identity)
        return identity

    @staticmethod
    def offer_link(request, provisioner, identity):
        """Fetch the setup link, if the realm can hand one back.

        A convenience rather than a requirement - the account exists either way
        and the setup email is a button on the list - so a realm that cannot
        mint links is reported and nothing else.
        """
        try:
            link = provisioner.issue_setup_link(identity, setup_redirect_uri())
        except (KeycloakError, ValueError) as error:
            logger.info("No setup link issued for %s: %s", identity.sub, error)
            return
        request.session[ISSUED_SESSION_KEY] = {
            "username": identity.user.username,
            "link": link,
        }

    # -- rendering ---------------------------------------------------------

    def again(self, request, form, error):
        context = self.page_context(request, form)
        context["error"] = error
        return render(request, self.template_name, context, status=400)

    @staticmethod
    def page_context(request, form):
        left = remaining(request.user)
        return {
            "form": form,
            "roles": invitable_roles(request.user),
            "remaining": left,
            "unlimited": left is None,
        }


class RevokeView(View):
    """Withdrawing trust from one account, on a page of its own.

    Separate from the list because it is not a row action in the sense the
    other two are: it asks for a reason, and it has to show by name everybody
    the cascade would reach. Both belong somewhere with room, and somewhere a
    reader can arrive at, read, and leave without having acted.
    """

    template_name = "qgis_sso/manage/revoke.html"

    def dispatch(self, request, *args, **kwargs):
        if not signed_in(request):
            return HttpResponseRedirect(f"{reverse('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        blast = self.blast_or_none(request, pk)
        if blast is None:
            return self.refuse(request)
        return render(request, self.template_name, self.context(blast, ""))

    def post(self, request, pk):
        blast = self.blast_or_none(request, pk)
        if blast is None:
            return self.refuse(request)

        reason = request.POST.get("reason", "").strip()
        if not reason:
            return render(
                request,
                self.template_name,
                dict(self.context(blast, reason), reason_missing=True),
                status=400,
            )

        try:
            revocation.revoke(request.user, blast.target, reason)
        except revocation.RevocationError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(reverse("qgis_sso:enrolment"))

        messages.success(
            request,
            _(
                "Trust withdrawn from %(username)s. %(count)d other account(s) "
                "suspended."
            )
            % {
                "username": blast.target.user.username,
                "count": len(blast.descendants),
            },
        )
        return HttpResponseRedirect(reverse("qgis_sso:enrolment"))

    @staticmethod
    def context(blast, reason):
        return {
            "blast": blast,
            "reason": reason,
            "grace_days": revocation.grace().days,
        }

    @staticmethod
    def blast_or_none(request, pk):
        """What revoking this account would do, or None if it may not be.

        ``preview`` applies the permission rule, so being able to see this page
        at all is the same check as being able to act on it. One answer for
        "no such account" and "not yours", so the page cannot be used to find
        out who exists.
        """
        identity = (
            KeycloakIdentity.objects.select_related("user", "sponsor")
            .filter(pk=pk)
            .first()
        )
        if identity is None:
            return None
        try:
            return revocation.preview(request.user, identity)
        except revocation.RevocationError:
            return None

    @staticmethod
    def refuse(request):
        messages.error(request, _("You cannot withdraw trust from that account."))
        return HttpResponseRedirect(reverse("qgis_sso:enrolment"))
