# The versions everything else builds on, and the only place that names them.
# The other parts receive these as module arguments.
{
  perSystem =
    { pkgs, ... }:

    let
      python = pkgs.python312;
    in

    {
      _module.args = {
        inherit python;

        pythonEnv = pkgs.callPackage ../python.nix { inherit python; };

        nodejs = pkgs.nodejs_22;

        postgresql = pkgs.postgresql_16.withPackages (ps: [ ps.postgis ]);

        geoEnv = import ../geo-env.nix {
          inherit (pkgs)
            gdal
            geos
            proj
            stdenv
            ;
        };
      };
    };
}
