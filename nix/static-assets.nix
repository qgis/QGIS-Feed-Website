# Webpack bundles, built from the committed package-lock.json.
{
  lib,
  buildNpmPackage,
  nodejs_22,
}:

buildNpmPackage {
  pname = "qgisfeed-static";
  version = "1.0.0";

  # buildNpmPackage would otherwise default to pkgs.nodejs. Pin the same Node
  # the devShell and the Dockerfiles use.
  nodejs = nodejs_22;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
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

  # A build-only asset pipeline, not an installable npm package.
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
