{
  description = "QGIS Home Page News Feed - development environment and application package";

  inputs = {
    # nixpkgs is pinned centrally for every QGIS repository. Bump it there.
    nixpkgs-version = {
      url = "github:QGIS/qgis-nixpkgs-version";
       # disable nixpkgs-unstable which is not used in this project
      inputs.nixpkgs-unstable.follows = "";
    };

    nixpkgs.follows = "nixpkgs-version/nixpkgs-26-05";

    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    process-compose-flake = {
      url = "github:Platonic-Systems/process-compose-flake";
    };

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs@{ flake-parts, ... }:

    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-darwin"
      ];

      imports = [
        inputs.treefmt-nix.flakeModule

        ./flake/checks.nix
        ./flake/formatter.nix
        ./flake/packages.nix
        ./flake/shells.nix
        ./flake/toolchain.nix
      ];
    };
}
