# The development stack, run by `dev` in the shell or by `nix run .#dev`. It sets
# up the same environment as the shell, which the processes inherit, and moves to
# the project root so the relative paths below resolve from any subdirectory.
{
  devEnv,
  fetchGeoip,
  lib,
  postgresql,
  scripts,
  ...
}:

{
  cli.preHook = ''
    ${lib.concatLines (lib.mapAttrsToList (n: v: "export ${n}=${lib.escapeShellArg v}") devEnv)}
    ${builtins.readFile ./scripts/dev-env.sh}
    ${builtins.readFile ./scripts/require-checkout.sh}
    cd "$PROJECT_ROOT"
  '';

  settings.processes = {
    db = {
      command = scripts.db;
      readiness_probe = {
        exec.command = "${postgresql}/bin/pg_isready --quiet";
        period_seconds = 2;
        failure_threshold = 30;
      };
      # SIGINT is PostgreSQL's fast shutdown; SIGTERM waits for clients to leave.
      shutdown.signal = 2;
    };

    db-init = {
      command = scripts.db-init;
      depends_on.db.condition = "process_healthy";
    };

    # Nothing depends on this: a missing GeoIP database only stops location
    # lookups resolving, so it must not block the server.
    geoip = {
      command = fetchGeoip;
      availability.restart = "no";
    };

    migrate = {
      command = "${scripts.manage}/bin/manage migrate --noinput";
      depends_on.db-init.condition = "process_completed_successfully";
    };

    # "compiled" matches a finished build cycle whether or not it had warnings,
    # which is exactly when webpack-stats.json exists.
    webpack = {
      command = scripts.webpack-watch;
      ready_log_line = "compiled";
    };

    django = {
      command = scripts.runserver;
      depends_on = {
        migrate.condition = "process_completed_successfully";
        webpack.condition = "process_log_ready";
      };
    };
  };
}
