# Webpack bundles built reproducibly from the committed package-lock.json.
#
# Produces:
#   $out/bundles            - hashed js/css, mirroring qgisfeedproject/static/bundles
#   $out/webpack-stats.json - the manifest django-webpack-loader reads
{ pkgs }:

pkgs.buildNpmPackage {
  pname = "qgisfeed-static";
  version = "1.0.0";

  # buildNpmPackage would otherwise default to pkgs.nodejs (24.x). Pin the same
  # Node the devShell and the Dockerfiles use, so the bundles shipped to
  # production are built by the toolchain they were developed against.
  nodejs = pkgs.nodejs_22;

  src = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ../package.json
      ../package-lock.json
      ../webpack.config.js
      ../qgisfeedproject/static
    ];
  };

  # Update with: nix run nixpkgs#prefetch-npm-deps -- package-lock.json
  npmDepsHash = "sha256-WhBX8o78hMaTdTsfejRLrWlhESaDQlvJ9VAIuvrv+VE=";

  # "npm run build" is webpack --mode production.
  npmBuildScript = "build";

  # This is a build-only asset pipeline, not an installable npm package.
  dontNpmInstall = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bundles
    cp -r qgisfeedproject/static/bundles/. $out/bundles/
    cp webpack-stats.json $out/webpack-stats.json

    runHook postInstall
  '';

  meta = {
    description = "Compiled webpack bundles for the QGIS Feed website";
  };
}
