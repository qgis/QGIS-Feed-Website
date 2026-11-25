from django.conf import settings


def settings_var(request):
    return {
        "MAIN_WEBSITE_URL": settings.MAIN_WEBSITE_URL,
        "LOCAL_LOGIN_ENABLED": settings.LOCAL_LOGIN_ENABLED,
        # The profile page offers it only to an account linked to SSO.
        "SSO_ACCOUNT_URL": settings.SSO_ACCOUNT_URL,
    }
