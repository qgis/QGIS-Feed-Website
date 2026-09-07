# The Python environment shared by the devShell and the application package.
#
# Everything comes from nixpkgs except three packages that nixpkgs does not
# carry, or carries at a version this project cannot use; those are built from
# real derivations in ./python-packages.nix rather than installed with pip, so
# that the production closure stays hermetic and reproducible.
#
# Keep the package list in step with REQUIREMENTS.txt.
{ pkgs }:

let
  python = pkgs.python312;

  # nixpkgs 26.05 ships Django 5.2 as the default `django` attribute (Django 4
  # was dropped at its April 2026 end of life), which is exactly what this
  # project targets. No override is needed, so every django-* package below
  # comes prebuilt from the binary cache.
  extraPackages = import ./python-packages.nix {
    inherit python;
    inherit (pkgs) lib;
  };

  pythonPackages =
    ps:
    [
      # Framework
      ps.django

      # Database
      ps.psycopg2

      # OIDC authentication
      ps.mozilla-django-oidc

      # Imaging
      ps.pillow
      ps.pilkit

      # GeoIP. maxminddb is a geoip2 dependency and is left unpinned so both
      # environments take whatever geoip2 asks for.
      ps.geoip2

      # HTTP and parsing
      ps.aiohttp
      ps.requests
      ps.beautifulsoup4

      # Social syndication
      ps.mastodon-py
      ps.atproto

      # Django add-ons available in nixpkgs
      ps.django-appconf
      ps.django-extensions
      ps.django-tinymce

      # User agent parsing
      ps.user-agents
      ps.ua-parser

      # Misc
      ps.sentry-sdk
      ps.sqlparse
    ]
    ++ [
      # Built here because nixpkgs cannot supply them at the required version.
      extraPackages.django-imagekit
      extraPackages.django-user-visit
      extraPackages.django-webpack-loader
    ];

  pythonEnv = python.withPackages pythonPackages;
in
{
  inherit
    python
    pythonEnv
    pythonPackages
    extraPackages
    ;
}
