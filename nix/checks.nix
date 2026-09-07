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

  # signals.py builds a GeoIP2() on every UserVisit save, outside the try that
  # guards the lookup, so the suite cannot run without a database on disk.
  # test_ip_address_removed goes further and asserts a real result - that
  # 180.247.213.170 resolves to Indonesia - so MaxMind's fabricated test
  # fixture is not enough and the actual GeoLite2 database is required.
  #
  # fetchurl is a fixed-output derivation, which is what lets it reach the
  # network from inside the build sandbox that ordinary derivations are walled
  # off from.
  #
  # A dated release tag, not the 'download' branch Dockerfile:29 uses. That
  # branch is force-pushed whenever MaxMind publishes, so it broke this check
  # with a hash mismatch every week, and it quietly re-dated the geolocation the
  # geofencing assertions above depend on. Release assets cannot be replaced in
  # place, so the tag pins both the build and the test data.
  #
  # The mirror is still a third party rather than MaxMind; moving to MaxMind's
  # Apache-2.0 test fixture needs the spatial fixtures rewritten off Indonesia.
  #
  # To move to a newer database, pick a tag from
  # https://github.com/P3TERX/GeoLite.mmdb/releases and regenerate the hash with
  #
  #   nix store prefetch-file --name GeoLite2-City.mmdb <url>
  geoipDb = pkgs.fetchurl {
    url = "https://github.com/P3TERX/GeoLite.mmdb/releases/download/2026.09.04/GeoLite2-City.mmdb";
    hash = "sha256-hZdM1xUzPB2rniP6BoVIOoyTFtNy5pFk+DbR+BLEH/g=";
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
