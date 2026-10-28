# Keep the package list in step with REQUIREMENTS.txt.
{
  callPackage,
  python312,
}:

let
  python = python312;

  extraPackages = callPackage ./python-packages.nix { inherit python; };

  pythonPackages =
    ps:
    [
      # Framework
      ps.django

      # Database
      ps.psycopg2

      # Imaging
      ps.pillow
      ps.pilkit

      # GeoIP. maxminddb is left unpinned so it follows what geoip2 asks for.
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
