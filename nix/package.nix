# The deployable QGIS Feed application.
#
# Produces:
#   $out/share/qgisfeed/qgisfeedproject  - the Django project
#   $out/share/qgisfeed/webpack-stats.json
#   $out/bin/qgisfeed-manage             - manage.py wrapper
#   $out/bin/qgisfeed-uwsgi              - uWSGI wrapper, the deployment entry
#
# nginx fronts the application with uwsgiPass, so the server has to speak the
# binary uwsgi protocol and qgisfeed-uwsgi is what the infrastructure runs. It
# serves plain HTTP too, with --http-socket, which covers local runs.
#
# No gunicorn entry point is built. The docker image runs gunicorn from
# REQUIREMENTS_PRODUCTION.txt and its own entrypoint, never from this closure,
# so a wrapper here would be a second, untested way to start the application.
#
# The layout mirrors the docker image on purpose: settings.py computes
# BASE_DIR as the qgisfeedproject directory and looks for webpack-stats.json
# one level above it, so the two must stay in that relationship.
{
  pkgs,
  pythonEnv,
  staticAssets,
  fetchGeoip,
}:

let
  version = "1.0.0";

  # GeoDjango loads these with ctypes at import time and cannot find them in the
  # Nix store on its own, so the wrappers below bake the paths in. Without them
  # `import django.contrib.gis` fails on the deployment host.
  soExt = pkgs.stdenv.hostPlatform.extensions.sharedLibrary;
  gdalLib = "${pkgs.gdal}/lib/libgdal${soExt}";
  geosLib = "${pkgs.geos}/lib/libgeos_c${soExt}";

  python = pkgs.python312;

  # uWSGI is built with only the python3 plugin. It embeds its own interpreter,
  # so the application's dependencies are put on PYTHONPATH by the wrapper
  # below rather than by building uWSGI against pythonEnv directly.
  uwsgi = pkgs.uwsgi.override {
    plugins = [ "python3" ];
    python3 = python;
  };

  # Only the sources the application actually needs. Excluding the docker,
  # node_modules and .nix trees keeps the closure small and stops unrelated
  # edits from triggering a rebuild.
  src = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ../qgisfeedproject
      ../REQUIREMENTS.txt
    ];
  };
in
pkgs.stdenv.mkDerivation {
  pname = "qgisfeed";
  inherit version src;

  nativeBuildInputs = [ pkgs.makeWrapper ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    appdir=$out/share/qgisfeed
    mkdir -p "$appdir"
    cp -r qgisfeedproject "$appdir/qgisfeedproject"

    # Webpack output. The bundles live inside STATICFILES_DIRS so that
    # collectstatic picks them up; the stats file sits one level above
    # BASE_DIR, where settings.py expects it.
    mkdir -p "$appdir/qgisfeedproject/static/bundles"
    cp -r ${staticAssets}/bundles/. "$appdir/qgisfeedproject/static/bundles/"
    cp ${staticAssets}/webpack-stats.json "$appdir/webpack-stats.json"

    # Media and static roots are runtime state and come from the systemd
    # StateDirectory, so drop anything that shipped in the source tree.
    rm -rf "$appdir/qgisfeedproject/media"

    mkdir -p $out/bin

    makeWrapper ${pythonEnv}/bin/python $out/bin/qgisfeed-manage \
      --add-flags "$appdir/qgisfeedproject/manage.py" \
      --prefix PYTHONPATH : "$appdir/qgisfeedproject" \
      --set-default DJANGO_SETTINGS_MODULE qgisfeedproject.settings \
      --set GDAL_LIBRARY_PATH ${gdalLib} \
      --set GEOS_LIBRARY_PATH ${geosLib} \
      --set PROJ_LIB ${pkgs.proj}/share/proj

    # uWSGI embeds its own interpreter, so both the project directory and the
    # dependency environment have to be on PYTHONPATH explicitly.
    makeWrapper ${uwsgi}/bin/uwsgi $out/bin/qgisfeed-uwsgi \
      --prefix PYTHONPATH : "$appdir/qgisfeedproject" \
      --prefix PYTHONPATH : "${pythonEnv}/${python.sitePackages}" \
      --set-default DJANGO_SETTINGS_MODULE qgisfeedproject.settings \
      --set GDAL_LIBRARY_PATH ${gdalLib} \
      --set GEOS_LIBRARY_PATH ${geosLib} \
      --set PROJ_LIB ${pkgs.proj}/share/proj \
      --chdir "$appdir/qgisfeedproject"

    runHook postInstall
  '';

  # Catches import errors and missing dependencies at build time rather than on
  # the deployment host. This is what caught GeoDjango failing to find libgdal.
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    export MEDIA_ROOT=$TMPDIR/media
    export STATIC_ROOT=$TMPDIR/static
    mkdir -p "$MEDIA_ROOT" "$STATIC_ROOT"

    $out/bin/qgisfeed-manage check

    # The uWSGI wrapper has its own PYTHONPATH, so prove that interpreter can
    # import the application too - a broken path here would otherwise only
    # surface when the service starts.
    $out/bin/qgisfeed-uwsgi --version >/dev/null

    runHook postInstallCheck
  '';

  # Facts about the application that a deployment would otherwise have to
  # hardcode. Reading them off the derivation keeps them owned by the people
  # who know the application, and stops several host configurations drifting
  # apart as it changes.
  passthru = {
    manageProgram = "qgisfeed-manage";
    uwsgiProgram = "qgisfeed-uwsgi";

    wsgiModule = "qgisfeedproject.wsgi";
    settingsModule = "qgisfeedproject.settings";

    # Base worker tuning. Contains no socket: that is a host decision, so the
    # deployment appends its own --socket or a second --ini.
    uwsgiIni = ./uwsgi.ini;

    # GeoLite2-City.mmdb is MaxMind licensed and cannot be shipped in the
    # closure, so it is fetched at runtime into a directory the deployment
    # chooses and points GEOIP_PATH at:
    #   ${fetchGeoip}/bin/fetch-geoip /var/lib/qgisfeed/geoip
    inherit fetchGeoip;
  };

  meta = {
    description = "QGIS Home Page News Feed - Django application";
    homepage = "https://github.com/qgis/QGIS-Feed-Website";
    mainProgram = "qgisfeed-uwsgi";
  };
}
