{ inputs, ... }:

{
  perSystem =
    {
      geoEnv,
      lib,
      nodejs,
      pkgs,
      postgresql,
      pythonEnv,
      self',
      ...
    }:

    let
      mkDevScript =
        name: runtimeInputs:
        pkgs.writeShellApplication {
          inherit name runtimeInputs;
          text =
            builtins.readFile ../nix/scripts/require-dev-shell.sh
            + builtins.readFile (../nix/scripts + "/${name}.sh");
        };

      djangoTools = [
        pythonEnv
        pkgs.git
        postgresql
      ];

      # To add a helper, write nix/scripts/<name>.sh and add a line here.
      scripts = lib.mapAttrs mkDevScript {
        db = [
          postgresql
          pkgs.coreutils
        ];
        db-init = [ postgresql ];
        db-reset = djangoTools;
        db-restore = djangoTools ++ [ pkgs.coreutils ];
        dev-help = [ pkgs.coreutils ];
        manage = djangoTools;
        runserver = djangoTools;
        tests = djangoTools;
        webpack-watch = djangoTools ++ [ nodejs ];
      };

      devEnv = geoEnv // {
        # What nix/scripts/require-dev-shell.sh looks for.
        QGISFEED_DEV_SHELL = "1";

        DJANGO_SETTINGS_MODULE = "qgisfeedproject.settings_nix";

        PGDATABASE = "qgisfeed";
      };

      devStack = (import inputs.process-compose-flake.lib { inherit pkgs; }).makeProcessCompose {
        name = "dev";
        modules = [
          ../nix/dev-stack.nix
          {
            _module.args = {
              inherit devEnv postgresql scripts;
              inherit (self'.packages) fetchGeoip;
            };
          }
        ];
      };
    in

    {
      apps.dev = {
        type = "app";
        program = lib.getExe devStack;
        meta.description = "Start the database, webpack and the development server";
      };

      devShells.default = pkgs.mkShell (
        devEnv
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

            devStack
            self'.packages.fetchGeoip
          ]
          ++ builtins.attrValues scripts;

          shellHook =
            builtins.readFile ../nix/scripts/dev-env.sh + builtins.readFile ../nix/scripts/shell-hook.sh;
        }
      );
    };
}
