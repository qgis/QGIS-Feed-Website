{
  description = "QGIS Home Page News Feed - development environment and application package";

  inputs = {
    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    nixpkgs.follows = "nixpkgs-version/nixpkgs-26-05";

    # nixpkgs is pinned centrally for every QGIS repository. Bump it there.
    nixpkgs-version.url = "github:QGIS/qgis-nixpkgs-version";

    process-compose-flake.url = "github:Platonic-Systems/process-compose-flake";

    services-flake.url = "github:juspay/services-flake";

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs@{ flake-parts, ... }:

    flake-parts.lib.mkFlake { inherit inputs; } {
      # Enable to get the flake-parts debug outputs.
      # https://flake.parts/options/flake-parts.html#opt-debug
      debug = false;

      systems = [
        "x86_64-linux"
        "aarch64-darwin"
        # "aarch64-linux"  # nothing here needs it
        # "x86_64-darwin"  # dropped by nixpkgs
      ];

      _module.args = {
        inherit inputs;
      };

      imports = [
        inputs.treefmt-nix.flakeModule

        ./flake/checks.nix
        ./flake/formatter.nix
        ./flake/packages.nix
        ./flake/shells.nix
      ];
    };
}
