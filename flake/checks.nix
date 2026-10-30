{
  perSystem =
    {
      lib,
      pkgs,
      postgresql,
      qgisfeed,
      ...
    }:

    {
      checks = {
        django-tests = pkgs.runCommand "django-tests" {
          nativeBuildInputs = [ postgresql ];

          app = qgisfeed;
          inherit (qgisfeed) manageProgram;

          # The package drops the media directory, so the fixture comes from here.
          fixtureImage = ../qgisfeedproject/media/feedimages/rust.png;

          # The only version of the override a checkout has.
          settingsTemplate = ../qgisfeedproject/qgisfeedproject/settings_local.py.templ;

          # MaxMind's own Apache-2.0 test database, pinned at a commit because a
          # fixed-output derivation needs a source that cannot change.
          # Bump with: nix store prefetch-file --name GeoLite2-City.mmdb <url>
          geoipDb = pkgs.fetchurl {
            url =
              "https://raw.githubusercontent.com/maxmind/MaxMind-DB/"
              + "d1551916cff0712d12ff602c309234471b0402a2/test-data/GeoLite2-City-Test.mmdb";
            hash = "sha256-i2tJg8DWxh0pP22koB8DfCK3xEV2IeZCiSINyGzSUG8=";
          };
        } (builtins.readFile ../nix/tests/django-tests.sh);
      }
      # A virtual machine needs KVM, so this one cannot run on darwin.
      // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
        nixos-service = pkgs.callPackage ../nix/tests/nixos-service.nix {
          inherit postgresql qgisfeed;
        };
      };
    };
}
