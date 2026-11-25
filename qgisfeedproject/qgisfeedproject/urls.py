"""qgisfeedproject URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path, re_path
from qgis_sso.views import RateLimitedCallbackView, admin_login, site_login

urlpatterns = [
    # Before admin.site.urls, so the admin's own login form is replaced.
    path("admin/login/", admin_login, name="admin_login_override"),
    path("admin/", admin.site.urls),
    re_path(r"^tinymce/", include("tinymce.urls")),
    path("", include("qgisfeed.urls")),
    path("accounts/login/", site_login, name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("sso/", include("qgis_sso.urls")),
    # Shadows the library's callback below with the rate-limited one.
    path(
        "oidc/callback/",
        RateLimitedCallbackView.as_view(),
        name="oidc_authentication_callback",
    ),
    path("oidc/", include("mozilla_django_oidc.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
