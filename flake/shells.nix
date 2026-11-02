# The development shell. `dev-help` lists what it provides.
{ inputs, ... }:

{
  perSystem =
    {
      geoEnv,
      nodejs,
      pkgs,
      postgresql,
      pythonEnv,
      self',
      ...
    }:

    let
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

      scripts = {
        db = mkScript "db" [
          postgresql
          pkgs.coreutils
        ];
        db-init = mkScript "db-init" [ postgresql ];
        db-reset = mkScript "db-reset" djangoTools;
        db-restore = mkScript "db-restore" (djangoTools ++ [ pkgs.coreutils ]);
        manage = mkScript "manage" djangoTools;
        runserver = mkScript "runserver" djangoTools;
        webpack-watch = mkScript "webpack-watch" (djangoTools ++ [ nodejs ]);
      };

      # makeProcessCompose rather than the flake module, so the stack is not
      # exported as a package that would start without the shell environment.
      devStack = (import inputs.process-compose-flake.lib { inherit pkgs; }).makeProcessCompose {
        name = "dev";
        modules = [
          ../nix/dev-stack.nix
          {
            _module.args = {
              inherit postgresql scripts;
              inherit (self'.packages) fetchGeoip;
            };
          }
        ];
      };
    in

    {
      devShells.default = pkgs.mkShell (
        geoEnv
        // {
          packages = [
            pythonEnv
            postgresql
            nodejs
            pkgs.gdal
            pkgs.geos
            pkgs.proj
            pkgs.libmaxminddb
            pkgs.pre-commit
            pkgs.curl
            pkgs.git

            # The helpers, each named after its own binary.
            devStack
            self'.packages.fetchGeoip
            scripts.db-reset
            scripts.db-restore
            scripts.manage
          ];

          # mkShell turns an unrecognised attribute into an environment variable.
          # These do not depend on the checkout; the ones that do are set by the
          # hook, which can run git.
          DJANGO_SETTINGS_MODULE = "qgisfeedproject.settings_nix";

          PGDATABASE = "qgisfeed";

          shellHook = builtins.readFile ../nix/scripts/shell-hook.sh;
        }
      );
    };
}
