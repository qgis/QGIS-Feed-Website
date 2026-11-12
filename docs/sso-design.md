# Why single sign-on works the way it does

These notes explain the rules behind sign-in with `auth.qgis.org`. Read
[sso.md](sso.md) first. Check here before you change one of these rules.

## Identity

### Roles are a copy

We rebuild the Django groups and the staff and superuser flags from the token
at every sign-in. Keycloak is the source of truth, so any local edit lasts only
until the next sign-in. The user admin says so beside the fields.

Mirroring only adds and removes the groups in `SSO_MANAGED_GROUPS`. A group
created by hand for something else is never touched.

### Superuser comes from a client role, never a realm role

A realm role would also make its holders on hub or plugins superusers here.
Roles on the `feed-qgis-org` client apply to this site only.

### `OIDC_CREATE_USER` must stay `False`

The realm federates the OSGeo LDAP directory. Creating an account from the
first token would give every person in it a feed account. The backend's
`create_user` refuses too, so both would have to be wrong at once.

### Client secrets live in `settings_local`

Anybody on the server can read the process environment, through the Nix store
and `systemctl show`. `OIDC_RP_CLIENT_SECRET` goes in `settings_local` instead.

## Migration

### We retire a password at the first SSO sign-in, and not before

That sign-in proves the person can reach their Keycloak account. Retiring the
password earlier could lock somebody out. Set `SSO_RETIRE_PASSWORD_ON_LOGIN`
to `False` to keep both ways in open if a cutover needs it.

### Local login needs break-glass access before it goes

With `LOCAL_LOGIN_ENABLED = False` and no tested emergency path, a Keycloak
outage locks out every administrator, including those who would fix Keycloak.
