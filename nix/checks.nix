# Checks run by `nix flake check`.
#
# Two tiers. Everything except `integration` is hermetic, needs no database and
# finishes in seconds; `integration` starts a PostGIS cluster in the build
# sandbox and runs the Django suite, so it takes minutes.
#
# No test logic lives in this file: each check reads a committed script from
# nix/tests and passes it what it needs through the derivation environment.
{
  pkgs,
  pythonEnv,
  qgisfeed,
  postgresql,
}:

let
  # The package installs the Django project one level below the share
  # directory; settings.py resolves BASE_DIR and webpack-stats.json from it.
  appPythonPath = "${qgisfeed}/share/qgisfeed/qgisfeedproject";

  # Attributes every check that drives the application needs. Reading the
  # program names off passthru rather than repeating them means a rename shows
  # up as a failing check instead of a check that quietly tests nothing.
  appAttrs = {
    app = qgisfeed;
    inherit (qgisfeed)
      manageProgram
      uwsgiProgram
      uwsgiIni
      wsgiModule
      settingsModule
      fetchGeoip
      ;
  };

  check =
    name: attrs: script:
    pkgs.runCommand "check-${name}" attrs (builtins.readFile script);

  # The geofencing tests need a real database on disk, so the check has to
  # fetch one. fetchurl is a fixed-output derivation, which is what lets it
  # reach the network from inside the build sandbox that ordinary derivations
  # are walled off from.
  #
  # MaxMind's own fabricated City fixture, pinned by commit SHA. Every other
  # source we tried rots: the P3TERX mirror's 'download' branch is force-pushed
  # whenever MaxMind publishes, its releases/latest asset is re-resolved to new
  # content just as often, and its dated releases are deleted after a while - so
  # a pinned hash broke CI on a cadence we do not control, and an immutable tag
  # was not on offer either. Here the SHA is part of the URL, so the location
  # and the content are both immutable: this fetch never needs updating.
  #
  # MaxMind-DB is dual Apache-2.0/MIT, test-data included, so unlike GeoLite2
  # itself this is redistributable.
  #
  # The fixture carries no Indonesian networks, so the assertions in tests.py
  # and the spatial_filter polygons in qgisfeed.json target London instead.
  # Changing one means changing the other.
  geoipDb = pkgs.fetchurl {
    url = "https://raw.githubusercontent.com/maxmind/MaxMind-DB/000a8df991543651637fd9c16b7a7f8480370514/test-data/GeoIP2-City-Test.mmdb";
    hash = "sha256-7ZcnOOTgOj5W4SBBpq9NkVkiSdEQ9+SmR+Xy+g5jnAk=";
  };
in
{
  # Fails the build if any committed shell script has a syntax error or a
  # shellcheck finding. SC1091 is excluded because common.sh is sourced through
  # a path resolved at runtime, which shellcheck cannot follow.
  shellcheck = pkgs.runCommand "shellcheck-scripts" { nativeBuildInputs = [ pkgs.shellcheck ]; } ''
    shellcheck -e SC1091 ${./scripts}/*.sh ${./tests}/*.sh
    touch $out
  '';

  # The same formatting the pre-commit hook applies, enforced where CI can see
  # it, so a checkout that never installed the hooks cannot drift.
  nixfmt = pkgs.runCommand "nixfmt-check" { nativeBuildInputs = [ pkgs.nixfmt ]; } ''
    nixfmt --check ${../flake.nix} ${../nix}/*.nix
    touch $out
  '';

  # The deployment interface: everything passthru advertises must exist.
  passthru-contract = check "passthru-contract" appAttrs ./tests/passthru-contract.sh;

  # The environment variables settings.py reads, which the infrastructure
  # depends on and CONTRIBUTING.md documents.
  settings-contract = check "settings-contract" {
    nativeBuildInputs = [ pythonEnv ];
    testScript = ./tests/settings_contract.py;
    inherit appPythonPath;
  } ./tests/settings-contract.sh;

  # Models and migrations agree.
  migration-drift = check "migration-drift" appAttrs ./tests/migration-drift.sh;

  # The WSGI callable imports inside uWSGI's embedded interpreter.
  uwsgi-app-load = check "uwsgi-app-load" appAttrs ./tests/uwsgi-app-load.sh;

  # Slow tier: the Django test suite and one real request, against PostGIS.
  integration = check "integration" (
    appAttrs
    // {
      nativeBuildInputs = [
        postgresql
        pythonEnv
        pkgs.curl
        pkgs.coreutils
      ];
      # The package drops the media directory, so the test fixture image
      # comes from the source tree.
      fixtureImage = ../qgisfeedproject/media/feedimages/rust.png;
      # The real override file is git-ignored, so a checkout only ever has the
      # template. Running the suite against it means a setting that is only
      # defined there - OIDC_RP_CLIENT_SECRET is the current example - is
      # exercised rather than silently absent, and the check rebuilds whenever
      # the template changes.
      settingsTemplate = ../qgisfeedproject/qgisfeedproject/settings_local_override.py.templ;
      inherit geoipDb;
    }
  ) ./tests/integration.sh;
}
