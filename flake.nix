{
  description = "QGIS Home Page News Feed - development environment and application package";

  inputs = {
    # nixpkgs is pinned centrally for all QGIS infrastructure repositories so
    # that projects share a binary cache and stay on compatible package sets.
    # Bump the version there, not here.
    nixpkgs-version.url = "github:QGIS/qgis-nixpkgs-version";
    nixpkgs.follows = "nixpkgs-version/nixpkgs-26-05";
  };

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));

      # PostGIS has to be built into the PostgreSQL environment, not merely
      # placed alongside it on PATH: the server resolves extension control
      # files under its own share/postgresql/extension, so a separate postgis
      # package is invisible to it and CREATE EXTENSION fails with
      # 'extension "postgis" is not available'.
      postgresqlFor = pkgs: pkgs.postgresql_16.withPackages (ps: [ ps.postgis ]);

      # Every helper is a committed dotfile under nix/scripts, wrapped so it
      # runs with a known set of tools on PATH. No shell logic lives in Nix.
      mkScript =
        pkgs: name: runtimeInputs:
        pkgs.writeShellApplication {
          inherit name runtimeInputs;
          text = builtins.readFile (./nix/scripts + "/${name}.sh");
          # common.sh is sourced at runtime from the checkout, so shellcheck
          # cannot see the variables it defines.
          excludeShellChecks = [ "SC1091" ];
        };

      # Fetches GeoLite2-City.mmdb into a directory given as its argument.
      # Exported as a package, not just a development app, because deployments
      # need it too: the database is MaxMind licensed and cannot ship in the
      # closure. It takes no dependency on a checkout.
      fetchGeoipFor =
        pkgs:
        mkScript pkgs "fetch-geoip" [
          pkgs.curl
          pkgs.coreutils
          pkgs.git
        ];

      # Built once per system and shared by packages, checks, apps and the
      # devShell, so the checks exercise the very derivation that is deployed
      # rather than a second one assembled the same way.
      appFor =
        pkgs:
        let
          inherit (import ./nix/python.nix { inherit pkgs; }) pythonEnv;
          staticAssets = import ./nix/static-assets.nix { inherit pkgs; };
          fetchGeoip = fetchGeoipFor pkgs;
        in
        {
          inherit pythonEnv staticAssets fetchGeoip;
          qgisfeed = import ./nix/package.nix {
            inherit
              pkgs
              pythonEnv
              staticAssets
              fetchGeoip
              ;
          };
        };
    in
    {
      devShells = forAllSystems (
        pkgs:
        let
          inherit (appFor pkgs) pythonEnv;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.nodejs_22
              (postgresqlFor pkgs)
              pkgs.gdal
              pkgs.geos
              pkgs.proj
              pkgs.libmaxminddb
              pkgs.pre-commit
              pkgs.curl
              pkgs.git
              pkgs.nixfmt
              pkgs.shellcheck
            ];

            # GeoDjango resolves these libraries with ctypes at import time and
            # cannot find them inside the Nix store without an explicit path.
            GDAL_LIBRARY_PATH = "${pkgs.gdal}/lib/libgdal${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
            GEOS_LIBRARY_PATH = "${pkgs.geos}/lib/libgeos_c${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
            PROJ_LIB = "${pkgs.proj}/share/proj";

            # The hook sources nix/scripts/common.sh, which needs to know where
            # the checkout is before PROJECT_ROOT has been established.
            shellHook = ''
              export PROJECT_ROOT_HINT="$PWD"
              source ${./nix/scripts/shell-hook.sh}
            '';
          };
        }
      );

      apps = forAllSystems (
        pkgs:
        let
          inherit (appFor pkgs) pythonEnv;

          script = mkScript pkgs;

          baseTools = [
            pythonEnv
            pkgs.git
            (postgresqlFor pkgs)
          ];

          mkApp = drv: {
            type = "app";
            program = "${drv}/bin/${drv.name}";
          };
        in
        {
          db-start = mkApp (script "db-start" baseTools);
          db-stop = mkApp (script "db-stop" baseTools);
          db-reset = mkApp (script "db-reset" baseTools);
          # coreutils supplies du/cut for the dump size shown in the prompt.
          db-restore = mkApp (script "db-restore" (baseTools ++ [ pkgs.coreutils ]));
          manage = mkApp (script "manage" baseTools);
          dev = mkApp (script "dev" (baseTools ++ [ pkgs.nodejs_22 ]));
          test = mkApp (script "test" baseTools);
          fetch-geoip = mkApp (fetchGeoipFor pkgs);
        }
      );

      packages = forAllSystems (
        pkgs:
        let
          app = appFor pkgs;
        in
        {
          inherit (app) staticAssets qgisfeed fetchGeoip;
          default = app.qgisfeed;
        }
      );

      # No nixosModule is exported on purpose. The QGIS infrastructure already
      # has a generic qgis.djangoApp module that owns PostgreSQL, nginx,
      # Metabase and the state directories; a second module here would be a
      # competing implementation. This flake supplies the application closure,
      # and the infrastructure decides how to run it.

      checks = forAllSystems (
        pkgs:
        let
          app = appFor pkgs;
        in
        import ./nix/checks.nix {
          inherit pkgs;
          inherit (app) pythonEnv qgisfeed;
          postgresql = postgresqlFor pkgs;
        }
      );

      # Wrapped rather than `pkgs.nixfmt` directly: nixfmt takes files, and
      # `nix fmt` hands its formatter the tree root as a bare '.', which nixfmt
      # answers by waiting on stdin forever.
      formatter = forAllSystems (
        pkgs:
        mkScript pkgs "format" [
          pkgs.nixfmt
          pkgs.findutils
          pkgs.git
        ]
      );
    };
}
