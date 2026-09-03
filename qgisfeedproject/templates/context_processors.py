from django.conf import settings


def settings_var(request):
    return {
        "MAIN_WEBSITE_URL": settings.MAIN_WEBSITE_URL,
        # The login page renders the local password form only while this is
        # set, so one flag turns the fallback off everywhere at once.
        "LOCAL_LOGIN_ENABLED": settings.LOCAL_LOGIN_ENABLED,
    }
