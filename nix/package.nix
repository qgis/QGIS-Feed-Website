# The deployable application: a manage.py wrapper and a uWSGI entry point.
{
  lib,
  stdenv,
  makeWrapper,
  uwsgi,
  geoEnv,
  python,
  pythonEnv,
  staticAssets,
  fetchGeoip,
}:

let
  geoEnvFlags = lib.concatStringsSep " " (lib.mapAttrsToList (n: v: "--set ${n} ${v}") geoEnv);

  # uWSGI embeds its own interpreter, so the application's dependencies go on
  # PYTHONPATH in the wrapper below rather than into this build.
  uwsgiWithPython = uwsgi.override {
    plugins = [ "python3" ];
    python3 = python;
  };
in
stdenv.mkDerivation {
  pname = "qgisfeed";
  version = "1.0.0";

  # Only the Django project. REQUIREMENTS.txt is not installed or read at
  # runtime: the dependencies come from pythonEnv. Media is runtime state and
  # comes from the systemd StateDirectory.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.difference ../qgisfeedproject ../qgisfeedproject/media;
  };

  nativeBuildInputs = [ makeWrapper ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    appdir=$out/share/qgisfeed
    mkdir -p "$appdir"
    cp -r qgisfeedproject "$appdir/qgisfeedproject"

    # The bundles live inside STATICFILES_DIRS so collectstatic picks them up;
    # the stats file sits one level above BASE_DIR.
    mkdir -p "$appdir/qgisfeedproject/static/bundles"
    cp -r ${staticAssets}/bundles/. "$appdir/qgisfeedproject/static/bundles/"
    cp ${staticAssets}/webpack-stats.json "$appdir/webpack-stats.json"

    mkdir -p $out/bin

    makeWrapper ${pythonEnv}/bin/python $out/bin/qgisfeed-manage \
      --add-flags "$appdir/qgisfeedproject/manage.py" \
      --prefix PYTHONPATH : "$appdir/qgisfeedproject" \
      --set-default DJANGO_SETTINGS_MODULE qgisfeedproject.settings \
      ${geoEnvFlags}

    makeWrapper ${uwsgiWithPython}/bin/uwsgi $out/bin/qgisfeed-uwsgi \
      --prefix PYTHONPATH : "$appdir/qgisfeedproject" \
      --prefix PYTHONPATH : "${pythonEnv}/${python.sitePackages}" \
      --set-default DJANGO_SETTINGS_MODULE qgisfeedproject.settings \
      ${geoEnvFlags} \
      --chdir "$appdir/qgisfeedproject"

    runHook postInstall
  '';

  # Catches import errors at build time rather than on the deployment host.
  # This is what caught GeoDjango failing to find libgdal.
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    export MEDIA_ROOT=$TMPDIR/media
    export STATIC_ROOT=$TMPDIR/static
    mkdir -p "$MEDIA_ROOT" "$STATIC_ROOT"

    $out/bin/qgisfeed-manage check
    $out/bin/qgisfeed-uwsgi --version >/dev/null

    runHook postInstallCheck
  '';

  # Facts a deployment would otherwise hardcode. Reading them off the
  # derivation stops several host configurations drifting apart.
  passthru = {
    manageProgram = "qgisfeed-manage";
    uwsgiProgram = "qgisfeed-uwsgi";

    wsgiModule = "qgisfeedproject.wsgi";
    settingsModule = "qgisfeedproject.settings";

    uwsgiIni = ./uwsgi.ini;

    # Build on its own with: nix build .#qgisfeed.staticAssets
    inherit staticAssets;

    # GeoLite2-City.mmdb is MaxMind licensed and cannot ship in the closure, so
    # a deployment fetches it into a directory it points GEOIP_PATH at:
    #   ${fetchGeoip}/bin/fetch-geoip /var/lib/qgisfeed/geoip
    inherit fetchGeoip;
  };

  meta = {
    description = "QGIS Home Page News Feed - Django application";
    homepage = "https://github.com/qgis/QGIS-Feed-Website";
    mainProgram = "qgisfeed-uwsgi";
  };
}
