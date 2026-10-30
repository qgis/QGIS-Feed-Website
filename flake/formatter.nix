# `nix fmt`. Python formatting stays with the pre-commit hook, which runs black
# and isort over the files you actually touch.
{
  perSystem = {
    treefmt = {
      projectRootFile = "flake.nix";

      programs = {
        nixfmt.enable = true;
        ruff-check.enable = true;
        shfmt.enable = true;
        # The repository indents shell with four spaces.
        shfmt.indent_size = 4;
      };

      # Pre-existing scripts that predate any formatter here.
      settings.global.excludes = [
        "entrypoint_production.sh"
        "entrypoint_testing.sh"
        "list-vscode-extensions.sh"
        "scripts/renew_ssl.sh"
        "vscode.sh"
      ];
    };
  };
}
