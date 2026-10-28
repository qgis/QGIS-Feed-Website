# The development shell. `dev-help` lists what it provides.
{
  perSystem =
    {
      devStack,
      fetchGeoip,
      pkgs,
      postgresql,
      pythonEnv,
      scripts,
      ...
    }:

    {
      devShells.default = pkgs.mkShell {
        packages = [
          pythonEnv
          postgresql
          pkgs.nodejs_22
          pkgs.gdal
          pkgs.geos
          pkgs.proj
          pkgs.libmaxminddb
          pkgs.pre-commit
          pkgs.curl
          pkgs.git

          # The helpers, each named after its own binary.
          devStack
          fetchGeoip
          scripts.db-reset
          scripts.db-restore
          scripts.manage
        ];

        # mkShell turns an unrecognised attribute into an environment variable.
        # These do not depend on the checkout; the ones that do are set by the
        # hook, which can run git.
        DJANGO_SETTINGS_MODULE = "qgisfeedproject.settings_nix";

        PGDATABASE = "qgisfeed";

        # GeoDjango resolves these with ctypes at import time and cannot find
        # them in the store without an explicit path.
        GDAL_LIBRARY_PATH = "${pkgs.gdal}/lib/libgdal${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
        GEOS_LIBRARY_PATH = "${pkgs.geos}/lib/libgeos_c${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
        PROJ_LIB = "${pkgs.proj}/share/proj";

        shellHook = builtins.readFile ../nix/scripts/shell-hook.sh;
      };
    };
}
