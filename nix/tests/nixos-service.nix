# Runs the closure under a real systemd. Minimal on purpose: nginx and ACME
# belong to the infrastructure's qgis.djangoApp module.
{
  testers,
  postgresql,
  qgisfeed,
}:

let
  database = "qgisfeed";

  serviceEnvironment = {
    DEBUG = "False";
    DOMAIN_NAME = "127.0.0.1";
    DB_NAME = database;
    DB_USER = database;
    DB_HOST = "/run/postgresql";
    MEDIA_ROOT = "/var/lib/qgisfeed/media";
    STATIC_ROOT = "/var/lib/qgisfeed/static";
    GEOIP_PATH = "/var/lib/qgisfeed/geoip";
    # Absent on a host: secrets arrive through EnvironmentFile.
    DJANGO_LOCAL_SETTINGS = "/var/lib/qgisfeed/settings_local.py";
  };
in
testers.runNixOSTest {
  name = "qgisfeed-service";

  nodes.machine = {
    # PostgreSQL and uWSGI together need more than the default.
    virtualisation.memorySize = 2048;

    services.postgresql = {
      enable = true;
      package = postgresql;
      ensureDatabases = [ database ];
      ensureUsers = [
        {
          name = database;
          ensureDBOwnership = true;
        }
      ];
    };

    users.users.${database} = {
      isSystemUser = true;
      group = database;
    };
    users.groups.${database} = { };

    systemd.services.qgisfeed-migrate = {
      description = "QGIS Feed database migration";
      wantedBy = [ "multi-user.target" ];
      requires = [ "postgresql.target" ];
      after = [ "postgresql.target" ];
      environment = serviceEnvironment;
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = database;
        StateDirectory = "qgisfeed";
      };
      script = ''
        ${qgisfeed}/bin/${qgisfeed.manageProgram} migrate --noinput
        ${qgisfeed}/bin/${qgisfeed.manageProgram} collectstatic --noinput
      '';
    };

    systemd.services.qgisfeed = {
      description = "QGIS Feed";
      wantedBy = [ "multi-user.target" ];
      requires = [ "qgisfeed-migrate.service" ];
      after = [ "qgisfeed-migrate.service" ];
      environment = serviceEnvironment;
      serviceConfig = {
        User = database;
        StateDirectory = "qgisfeed";
        # Two workers, not the eight the shipped ini asks for.
        ExecStart = ''
          ${qgisfeed}/bin/${qgisfeed.uwsgiProgram} \
            --ini ${qgisfeed.uwsgiIni} \
            --http-socket 127.0.0.1:8000 \
            --need-app \
            --workers 2 \
            --cheaper 0
        '';
      };
    };
  };

  testScript = ''
    machine.wait_for_unit("qgisfeed-migrate.service")
    machine.wait_for_unit("qgisfeed.service")
    machine.wait_for_open_port(8000)

    # Reaching this proves the unit started, found its settings and reached
    # PostgreSQL.
    machine.succeed("curl --fail --silent 'http://127.0.0.1:8000/?json=1' -o /tmp/feed.json")
    machine.succeed("python3 -c 'import json; json.load(open(\"/tmp/feed.json\"))'")
  '';
}
