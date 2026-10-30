{
  perSystem =
    {
      geoEnv,
      nodejs,
      pkgs,
      python,
      pythonEnv,
      ...
    }:

    let
      staticAssets = pkgs.callPackage ../nix/static-assets.nix { inherit nodejs; };

      # Exported as a package, not just a dev helper: a deployment needs it
      # too, because the MaxMind database cannot ship in the closure.
      fetchGeoip = pkgs.writeShellApplication {
        name = "fetch-geoip";
        runtimeInputs = [
          pkgs.curl
          pkgs.coreutils
          pkgs.git
        ];
        text = builtins.readFile ../nix/scripts/fetch-geoip.sh;
      };

      qgisfeed = pkgs.callPackage ../nix/package.nix {
        inherit
          fetchGeoip
          geoEnv
          python
          pythonEnv
          staticAssets
          ;
      };
    in

    {
      packages = {
        inherit qgisfeed fetchGeoip;
        default = qgisfeed;
      };
    };
}
