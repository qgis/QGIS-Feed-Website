# The development stack, run by `dev` inside `nix develop`, from which the
# processes inherit their environment. Built with makeProcessCompose rather
# than the flake module, so there is no runnable output that would start
# without that environment.
{
  fetchGeoip,
  scripts,
  servicesFlakeModule,
}:

{ pkgs, ... }:

{
  imports = [ servicesFlakeModule ];

  services.postgres.db = {
    enable = true;
    package = pkgs.postgresql_16;
    extensions = ps: [ ps.postgis ];
    dataDir = ".nix/pgdata";
    socketDir = ".nix/run";
    # Unix socket only, so this cannot clash with a system PostgreSQL or be
    # reached from another machine.
    listen_addresses = "";
    initialScript.before = ./postgis.sql;
    initialDatabases = [ { name = "qgisfeed"; } ];
  };

  settings.processes = {
    # Nothing depends on this: a missing GeoIP database only stops location
    # lookups resolving, so it must not block the server.
    geoip = {
      command = fetchGeoip;
      availability.restart = "no";
    };

    migrate = {
      command = "${scripts.manage}/bin/manage migrate --noinput";
      depends_on.db.condition = "process_healthy";
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
