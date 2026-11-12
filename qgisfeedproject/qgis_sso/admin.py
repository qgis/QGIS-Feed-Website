# coding=utf-8
"""Admin views over the identity bindings and the audit trail.

Both are read-only. The login path writes them.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import KeycloakIdentity, SsoAuditEvent


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


@admin.register(KeycloakIdentity)
class KeycloakIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "preferred_username",
        "link_method",
        "linked_at",
        "first_sso_login_at",
    )
    list_filter = ("link_method", "linked_at", "first_sso_login_at")
    search_fields = ("user__username", "user__email", "preferred_username", "sub")
    # Editing a subject by hand would point the account at another person.
    readonly_fields = tuple(
        field.name for field in KeycloakIdentity._meta.fields if field.name != "id"
    )
    ordering = ("-linked_at",)

    def has_add_permission(self, request):
        return False


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

    @admin.display(boolean=True, description=_("SSO"))
    def sso_linked(self, obj):
        return hasattr(obj, "keycloak_identity")

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
