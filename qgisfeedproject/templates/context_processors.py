from django.conf import settings


def settings_var(request):
    return {
        "MAIN_WEBSITE_URL": settings.MAIN_WEBSITE_URL,
        # The login page renders the local password form only while this is
        # set, so one flag turns the fallback off everywhere at once.
        "LOCAL_LOGIN_ENABLED": settings.LOCAL_LOGIN_ENABLED,
        # Only meaningful for an SSO-linked account; the header checks that
        # before offering the link.
        "SSO_ACCOUNT_URL": settings.SSO_ACCOUNT_URL,
    }
