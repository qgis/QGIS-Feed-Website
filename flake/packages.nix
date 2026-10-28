# The derivations. What several parts need is published via _module.args.
{ inputs, ... }:

{
  perSystem =
    { pkgs, ... }:

    let
      inherit (pkgs.callPackage ../nix/python.nix { }) pythonEnv;

      staticAssets = pkgs.callPackage ../nix/static-assets.nix { };

      # PostGIS has to be built into the PostgreSQL environment rather than sit
      # beside it on PATH: the server resolves extension control files under
      # its own share directory.
      postgresql = pkgs.postgresql_16.withPackages (ps: [ ps.postgis ]);

      # Every helper is a committed script under nix/scripts, wrapped so it
      # runs with a known set of tools on PATH. No shell logic lives in Nix.
      mkScript =
        name: runtimeInputs:
        pkgs.writeShellApplication {
          inherit name runtimeInputs;
          text = builtins.readFile (../nix/scripts + "/${name}.sh");
        };
      djangoTools = [
        pythonEnv
        pkgs.git
        postgresql
      ];

      # Exported as a package, not just a dev helper: a deployment needs it
      # too, because the MaxMind database cannot ship in the closure.
      fetchGeoip = mkScript "fetch-geoip" [
        pkgs.curl
        pkgs.coreutils
        pkgs.git
      ];

      qgisfeed = pkgs.callPackage ../nix/package.nix {
        inherit pythonEnv staticAssets fetchGeoip;
      };

      scripts = {
        db-reset = mkScript "db-reset" djangoTools;
        db-restore = mkScript "db-restore" (djangoTools ++ [ pkgs.coreutils ]);
        manage = mkScript "manage" djangoTools;
        runserver = mkScript "runserver" djangoTools;
        webpack-watch = mkScript "webpack-watch" (djangoTools ++ [ pkgs.nodejs_22 ]);
      };
      # makeProcessCompose rather than the flake module, so the stack is not
      # exported as a package that would start without the shell environment.
      devStack = (import inputs.process-compose-flake.lib { inherit pkgs; }).makeProcessCompose {
        name = "dev";
        modules = [
          (import ../nix/dev-stack.nix {
            inherit fetchGeoip scripts;
            servicesFlakeModule = inputs.services-flake.processComposeModules.default;
          })
        ];
      };
    in

    {
      _module.args = {
        inherit
          devStack
          fetchGeoip
          postgresql
          pythonEnv
          qgisfeed
          scripts
          ;
      };

      packages = {
        inherit qgisfeed staticAssets fetchGeoip;
        default = qgisfeed;
      };
    };
}
