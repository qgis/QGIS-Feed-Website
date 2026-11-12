# coding=utf-8
"""Admin views over the identity bindings and the audit trail.

The account migration runs from here, so it never needs a shell on the server.
Each step is its own action. :mod:`qgis_sso.provisioning` and
:mod:`qgis_sso.migration` decide, and this module confirms and reports.
"""

import csv

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .actions import is_sso_administrator as user_is_sso_administrator
from .actions import (
    max_users_per_action,
    plan_provisioning,
    retire_passwords,
    run_provisioning,
    send_setup_emails,
)
from .keycloak import KeycloakError
from .migration import REPORT_FIELDS, flag_users, report_row
from .models import KeycloakIdentity, SsoAuditEvent
from .provisioning import (
    ALREADY_DISABLED,
    DISABLED,
    NOT_YET_PROVEN,
    Provisioner,
    setup_redirect_uri,
)


class SsoLinkedFilter(admin.SimpleListFilter):
    """Filter the user list by whether an account has a Keycloak identity.

    With the *last login* filter, it shows which active accounts still need
    migrating.
    """

    title = _("SSO account")
    parameter_name = "sso_linked"

    def lookups(self, request, model_admin):
        return (("yes", _("Linked")), ("no", _("Not linked")))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(keycloak_identity__isnull=False)
        if self.value() == "no":
            return queryset.filter(keycloak_identity__isnull=True)
        return queryset


def is_sso_administrator(request):
    """Superuser-only, defined once in :mod:`qgis_sso.actions`."""
    return user_is_sso_administrator(request.user)


@admin.register(KeycloakIdentity)
class KeycloakIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "preferred_username",
        "link_method",
        "linked_at",
        "first_sso_login_at",
        "setup_email_sent_at",
    )
    list_filter = ("link_method", "linked_at", "first_sso_login_at")
    search_fields = ("user__username", "user__email", "preferred_username", "sub")
    # Editing a subject by hand would point the account at another person.
    readonly_fields = tuple(
        field.name for field in KeycloakIdentity._meta.fields if field.name != "id"
    )
    ordering = ("-linked_at",)
    actions = ["send_setup_email", "disable_local_password"]

    def get_readonly_fields(self, request, obj=None):
        # Django appends readonly fields to the form, so this places it on the page.
        return tuple(super().get_readonly_fields(request, obj)) + ("in_keycloak",)

    @admin.display(description=_("Fallback when email does not arrive"))
    def in_keycloak(self, obj):
        """Link to this user in the Keycloak admin console.

        Keycloak never hands back the setup link, which only leaves by email.
        When mail does not arrive, the console is the fallback.
        """
        if not obj or not obj.sub:
            return "-"
        url = getattr(settings, "SSO_KEYCLOAK_USER_CONSOLE_URL", "").format(sub=obj.sub)
        if not url:
            return "-"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a> {}',
            url,
            _("Open this account in the Keycloak admin console"),
            _(
                "The setup link cannot be retrieved. It exists only in the email "
                "Keycloak sends."
            ),
        )

    def has_add_permission(self, request):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not is_sso_administrator(request):
            for name in ("send_setup_email", "disable_local_password"):
                actions.pop(name, None)
        return actions

    @admin.action(description=_("Send the account-setup email"))
    def send_setup_email(self, request, queryset):
        """Send a setup link, the first time or again.

        A link expires after 14 days, and a passkey-only account has no
        password to fall back on.
        """
        if not is_sso_administrator(request):
            self.message_user(
                request,
                _("You do not have permission to send setup emails."),
                messages.ERROR,
            )
            return

        try:
            redirect_uri = setup_redirect_uri()
            provisioner = Provisioner()
        except ValueError as error:
            self.message_user(request, str(error), messages.ERROR)
            return
        except KeycloakError as error:
            self.message_user(
                request, _("Could not reach Keycloak: %s") % error, messages.ERROR
            )
            return

        run = send_setup_emails(
            provisioner, queryset.select_related("user"), redirect_uri
        )
        for outcome in run.problems:
            self.message_user(
                request,
                _("%(username)s: %(error)s")
                % {"username": outcome.username, "error": outcome.message},
                messages.ERROR,
            )
        if run.succeeded:
            self.message_user(
                request,
                _("Sent %d setup email(s).") % len(run.succeeded),
                messages.SUCCESS,
            )

    @admin.action(description=_("Disable the local Django password"))
    def disable_local_password(self, request, queryset):
        """Retire the password for people SSO already works for.

        The rest are reported and left alone.
        """
        if not is_sso_administrator(request):
            self.message_user(
                request,
                _("You do not have permission to disable passwords."),
                messages.ERROR,
            )
            return

        _run, counts = retire_passwords(queryset.select_related("user"))

        if counts[DISABLED] or counts[ALREADY_DISABLED]:
            self.message_user(
                request,
                _(
                    "Disabled %(disabled)d local password(s). %(already)d already "
                    "had none."
                )
                % {
                    "disabled": counts[DISABLED],
                    "already": counts[ALREADY_DISABLED],
                },
                messages.SUCCESS,
            )
        if counts[NOT_YET_PROVEN]:
            self.message_user(
                request,
                _(
                    "Left %d account(s) alone: they have never signed in through "
                    "SSO, so removing the password would lock them out."
                )
                % counts[NOT_YET_PROVEN],
                messages.WARNING,
            )


@admin.register(SsoAuditEvent)
class SsoAuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "username", "sub")
    list_filter = ("action", "created_at")
    search_fields = ("username", "sub")
    readonly_fields = ("created_at", "action", "user", "username", "sub", "detail")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # The audit trail cannot be edited from the interface it audits.
        return False


class SsoAwareUserAdmin(BaseUserAdmin):
    """The stock user admin, warning that SSO accounts get roles from Keycloak."""

    list_display = BaseUserAdmin.list_display + ("sso_linked",)
    list_select_related = ("keycloak_identity",)
    list_filter = BaseUserAdmin.list_filter + (SsoLinkedFilter,)
    actions = ["provision_in_keycloak", "export_migration_report"]

    @admin.display(boolean=True, description=_("SSO"))
    def sso_linked(self, obj):
        return hasattr(obj, "keycloak_identity")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not is_sso_administrator(request):
            actions.pop("provision_in_keycloak", None)
        return actions

    @admin.action(description=_("Export the migration report for selected users"))
    def export_migration_report(self, request, queryset):
        """Download what provisioning would do to each account. Changes nothing.

        The flags mark the accounts a person has to decide about.
        """
        selected = list(queryset.prefetch_related("groups").order_by("username"))
        flags = flag_users(selected)
        linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))

        response = HttpResponse(content_type="text/csv")
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        response["Content-Disposition"] = (
            f'attachment; filename="sso-migration-report-{stamp}.csv"'
        )
        writer = csv.DictWriter(response, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for user in selected:
            writer.writerow(report_row(user, flags.get(user.pk, []), linked))
        return response

    @admin.action(description=_("Create the Keycloak account"))
    def provision_in_keycloak(self, request, queryset):
        """Create or link realm accounts for the selected users. Sends no email.

        The first pass renders the confirmation page. Only a POST carrying
        ``confirm`` writes anything.
        """
        if not is_sso_administrator(request):
            self.message_user(
                request,
                _("You do not have permission to provision accounts."),
                messages.ERROR,
            )
            return None

        users = list(queryset.prefetch_related("groups").order_by("username"))
        limit = max_users_per_action()
        if len(users) > limit:
            self.message_user(
                request,
                _(
                    "You selected %(count)d users, but one run takes at most "
                    "%(limit)d. Each account costs several calls to Keycloak, "
                    "so work in smaller waves."
                )
                % {"count": len(users), "limit": limit},
                messages.ERROR,
            )
            return None

        include_flagged = request.POST.get("include_flagged") == "yes"
        try:
            provisioner, decisions = plan_provisioning(users, include_flagged)
        except KeycloakError as error:
            self.message_user(
                request,
                _("Could not reach Keycloak: %s") % error,
                messages.ERROR,
            )
            return None

        if request.POST.get("confirm") != "yes":
            return render(
                request,
                "admin/qgis_sso/provision_confirmation.html",
                {
                    **self.admin_site.each_context(request),
                    "title": _("Provision accounts in Keycloak"),
                    "opts": self.model._meta,
                    "decisions": decisions,
                    "actionable": [d for d in decisions if d.actionable],
                    "action_checkbox_name": ACTION_CHECKBOX_NAME,
                    "selected": [str(user.pk) for user in users],
                    "include_flagged": include_flagged,
                    "media": self.media,
                },
            )

        run = run_provisioning(provisioner, decisions)
        for outcome in run.problems:
            self.message_user(
                request,
                _("%(username)s: %(reason)s")
                % {"username": outcome.username, "reason": outcome.message},
                messages.ERROR if outcome.failed else messages.WARNING,
            )
        provisioned = len(run.succeeded)
        failed = len([outcome for outcome in run.problems if outcome.failed])

        if provisioned:
            self.message_user(
                request,
                _(
                    "Created or linked %d QGIS account(s). Nobody has been "
                    "emailed yet. Select them under Keycloak identities and send "
                    "the account-setup email."
                )
                % provisioned,
                messages.SUCCESS if not failed else messages.WARNING,
            )
        elif not failed:
            self.message_user(
                request,
                _("Nothing to do: none of the selected users could be provisioned."),
                messages.INFO,
            )
        return None

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj=obj, **kwargs)
        if obj is not None and hasattr(obj, "keycloak_identity"):
            notice = _(
                "This account signs in through auth.qgis.org. Its groups, "
                "staff and superuser flags are overwritten from its Keycloak "
                "roles at the next sign-in, so change them in Keycloak, not "
                "here."
            )
            for field_name in ("groups", "is_staff", "is_superuser"):
                field = form.base_fields.get(field_name)
                if field is not None:
                    field.help_text = notice
        return form


admin.site.unregister(User)
admin.site.register(User, SsoAwareUserAdmin)
