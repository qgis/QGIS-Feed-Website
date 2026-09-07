# coding=utf-8
"""Admin views over the identity bindings and the audit trail.

The whole account migration runs from here, so it never needs a shell on the
server: export the report, create the realm accounts, send the setup emails,
retire the local passwords. Each is a separate action, so no step is a side
effect of another. :mod:`qgis_sso.provisioning` and :mod:`qgis_sso.migration`
decide; this module asks for confirmation and reports what happened.
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

from .keycloak import KeycloakError
from .migration import REPORT_FIELDS, flag_users, report_row
from .models import KeycloakIdentity, SsoAuditEvent
from .provisioning import (
    ALREADY_DISABLED,
    DISABLED,
    NOT_YET_PROVEN,
    Provisioner,
    disable_local_password,
    setup_redirect_uri,
)

#: Each user costs several synchronous round trips to Keycloak, inside one
#: request. Provision in waves of this size; there is no task queue here.
DEFAULT_MAX_USERS_PER_ACTION = 25


def max_users_per_action():
    return getattr(settings, "SSO_ADMIN_ACTION_MAX_USERS", DEFAULT_MAX_USERS_PER_ACTION)


class SsoLinkedFilter(admin.SimpleListFilter):
    """Filter the user list by whether an account has a Keycloak identity.

    Combined with the *last login* filter this answers the question the
    migration is gated on: which accounts still in use have nobody behind them
    in the realm yet.
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
    """Who may write to the shared realm or retire a login method.

    Superusers only. The service account these actions use can create realm
    users and assign client roles, so anyone able to trigger them can grant
    privileges on this site.
    """
    return request.user.is_active and request.user.is_superuser


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
    # Everything here is written by the login path or by provisioning. Editing
    # a subject by hand would repoint a site account at a different person, so
    # the whole record is read-only.
    readonly_fields = tuple(
        field.name for field in KeycloakIdentity._meta.fields if field.name != "id"
    )
    ordering = ("-linked_at",)
    actions = ["send_setup_email", "disable_local_password"]

    def get_readonly_fields(self, request, obj=None):
        # Django's get_fields() appends the readonly fields, so naming it here
        # is enough to place it on the detail page.
        return tuple(super().get_readonly_fields(request, obj)) + ("in_keycloak",)

    @admin.display(description=_("Fallback when email does not arrive"))
    def in_keycloak(self, obj):
        """Deep link to this user in the Keycloak admin console.

        Keycloak has no API that hands back the setup link - the action token
        is minted inside ``execute-actions-email`` and only ever leaves by
        email. When mail is not arriving, the console is where an administrator
        can see the credential state and retry against Keycloak's own SMTP.
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
                "— the setup link itself cannot be retrieved; it exists only "
                "inside the email Keycloak sends."
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
        """Send a setup link, first time or later.

        Separate from provisioning so it can be triggered whenever it is
        needed: the link expires after 14 days, and a passkey-only account has
        no password to fall back on, so somebody who misses the window or
        loses every device has no way back in without this.
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

        sent = 0
        for identity in queryset.select_related("user"):
            try:
                provisioner.send_setup_link(identity, redirect_uri)
            except KeycloakError as error:
                self.message_user(
                    request,
                    _("%(username)s: %(error)s")
                    % {"username": identity.user.username, "error": error},
                    messages.ERROR,
                )
                continue
            sent += 1

        if sent:
            self.message_user(
                request, _("Sent %d setup email(s).") % sent, messages.SUCCESS
            )

    @admin.action(description=_("Disable the local Django password"))
    def disable_local_password(self, request, queryset):
        """Retire the password fallback for people SSO already works for.

        Only accounts with a recorded successful SSO login are touched. The
        rest are reported and left alone, because taking the password from
        somebody who has not yet signed in through Keycloak locks them out of
        an account they cannot recover on their own.
        """
        if not is_sso_administrator(request):
            self.message_user(
                request,
                _("You do not have permission to disable passwords."),
                messages.ERROR,
            )
            return

        counts = {DISABLED: 0, ALREADY_DISABLED: 0, NOT_YET_PROVEN: 0}
        for identity in queryset.select_related("user"):
            counts[disable_local_password(identity)] += 1

        if counts[DISABLED] or counts[ALREADY_DISABLED]:
            self.message_user(
                request,
                _(
                    "Disabled %(disabled)d local password(s); %(already)d already "
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
        # An audit trail that can be edited from the interface it audits is
        # not an audit trail.
        return False


class SsoAwareUserAdmin(BaseUserAdmin):
    """The stock user admin, plus a warning about mirrored permissions.

    For an SSO-linked account the group and staff checkboxes are a projection
    of the Keycloak roles and are recomputed on every sign-in, so editing them
    here appears to work and then silently reverts. Saying so beside the
    controls is the only place a maintainer will actually read it.
    """

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

        Read before provisioning anybody. The flags are the accounts that
        cannot be migrated unattended, `username-case-collision` and
        `duplicate-email` most of all.
        """
        # Flags are computed against the whole population: a username case
        # collision is only visible when the other account is in view too.
        flags = flag_users(User.objects.all().prefetch_related("groups"))
        linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))

        response = HttpResponse(content_type="text/csv")
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        response["Content-Disposition"] = (
            f'attachment; filename="sso-migration-report-{stamp}.csv"'
        )
        writer = csv.DictWriter(response, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for user in queryset.prefetch_related("groups").order_by("username"):
            writer.writerow(report_row(user, flags.get(user.pk, []), linked))
        return response

    @admin.action(description=_("Create the Keycloak account"))
    def provision_in_keycloak(self, request, queryset):
        """Create realm accounts for the selected users. Sends no email.

        Sending is a separate action on Keycloak identities, so that inviting
        somebody is never a side effect of creating their account and can be
        repeated whenever a link expires.

        Runs in two passes. The first renders what would happen, per user, and
        is the only preview there is. Only a POST carrying ``confirm`` writes
        anything.
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
                    "Selected %(count)d users, which is more than the limit of "
                    "%(limit)d for one run. Provision them in smaller waves: "
                    "each account costs several calls to Keycloak and the "
                    "request would time out part-way through."
                )
                % {"count": len(users), "limit": limit},
                messages.ERROR,
            )
            return None

        try:
            provisioner = Provisioner()
        except KeycloakError as error:
            self.message_user(
                request,
                _("Could not reach Keycloak: %s") % error,
                messages.ERROR,
            )
            return None

        # Flags are computed against the whole population, not the selection:
        # a username case collision is only visible when the other account is
        # in view too.
        flags = flag_users(User.objects.all().prefetch_related("groups"))
        linked = set(KeycloakIdentity.objects.values_list("user_id", flat=True))
        include_flagged = request.POST.get("include_flagged") == "yes"

        decisions = [
            provisioner.inspect(
                user,
                flags=flags.get(user.pk, []),
                include_flagged=include_flagged,
                linked_ids=linked,
            )
            for user in users
        ]

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

        provisioned = failed = 0
        for decision in decisions:
            if not decision.actionable:
                # Name the account and the reason. Being told only that there
                # was nothing to do leaves no way to see which account was
                # held back, or for what, without previewing again.
                self.message_user(
                    request,
                    _("%(username)s: %(reason)s")
                    % {"username": decision.user.username, "reason": decision.reason},
                    messages.ERROR if decision.is_error else messages.WARNING,
                )
                continue
            try:
                provisioner.provision(decision)
            except KeycloakError as error:
                failed += 1
                self.message_user(
                    request,
                    _("%(username)s: could not be created: %(error)s")
                    % {"username": decision.username, "error": error},
                    messages.ERROR,
                )
                continue
            provisioned += 1

        if provisioned:
            self.message_user(
                request,
                _(
                    "Created %d Keycloak account(s). Nobody has been emailed yet: "
                    "select them under Keycloak identities and send the "
                    "account-setup email."
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
