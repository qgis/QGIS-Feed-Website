# Settings local for docker compose production settings
import os

DEBUG = False

ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.getenv("QGISFEED_DOCKER_DBNAME", "qgisfeed"),
        "USER": os.getenv("QGISFEED_DOCKER_DBUSER", "docker"),
        "PASSWORD": os.getenv("QGISFEED_DOCKER_DBPASSWORD", "docker"),
        "HOST": os.getenv("QGISFEED_DOCKER_DBHOST", "postgis"),
        "PORT": "5432",
    }
}

MEDIA_ROOT = os.getenv("MEDIA_ROOT", "/shared-volume/media/")
MEDIA_URL = "/media/"
STATIC_ROOT = os.getenv("STATIC_ROOT", "/shared-volume/static/")
STATIC_URL = "/static/"

if not os.path.exists(MEDIA_ROOT):
    os.mkdir(MEDIA_ROOT)

if not os.path.exists(STATIC_ROOT):
    os.mkdir(STATIC_ROOT)

# settings for enabling https forwarding
USE_X_FORWARDED_PORT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

QGIS_FEED_PROD_URL = os.environ.get("QGIS_FEED_PROD_URL", False)
CSRF_TRUSTED_ORIGINS = [
    p + QGIS_FEED_PROD_URL for p in ["http://", "https://"] if QGIS_FEED_PROD_URL
]
CORS_ORIGIN_WHITELIST = CSRF_TRUSTED_ORIGINS
