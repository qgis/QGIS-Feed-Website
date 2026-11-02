# The development stack, run by `dev` inside `nix develop`, from which the
# processes inherit their environment. Built with makeProcessCompose rather
# than the flake module, so there is no runnable output that would start
# without that environment.
{
  fetchGeoip,
  postgresql,
  scripts,
  ...
}:

{
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
