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
and `systemctl show`. `OIDC_RP_CLIENT_SECRET` and
`SSO_PROVISIONER_CLIENT_SECRET` go in `settings_local` instead.

Provisioning uses its own service account. The web client never holds
`manage-users`, so a compromised site cannot change the realm.

## Enrolment

### Linking matches on a verified address and nothing else

A username can belong to somebody else, so it proves nothing. We refuse an
address that differs and an address the realm has not verified. The page says
which one it was, because you fix them in different ways.

We also refuse a realm account that is already linked to somebody here. Two
local accounts on one subject would break every lookup in the app.

`proposed_roles` works out the client role from what the account can already do
here, so linking grants no new privilege.

## Passkeys

### This site never changes a credential

WebAuthn ties a credential to the site that created it, so no other site can
register or delete a passkey for the realm. A browser will refuse. The profile
page reads the list through the provisioner service account and does nothing
else. The user authorises every change at Keycloak through `kc_action`, an
application-initiated action, and a view that has already checked the request
puts that action in the session. A crafted link cannot ask Keycloak to run an
action of its own choosing.

### We protect the last passkey

These accounts have no password. Removing the only passkey would lock that
person out until an administrator sends a new setup email.

## Migration

### The four admin actions stay separate

Each one does a single thing, so no step happens as a side effect of another.
Creating an account never emails anybody, and you can invite somebody again
without touching their account.

### We retire a password at the first SSO sign-in, and not before

That sign-in proves the person can reach their Keycloak account. Retiring the
password earlier could lock somebody out, so the backfill action skips those
accounts and reports them. Set `SSO_RETIRE_PASSWORD_ON_LOGIN` to `False` to
keep both ways in open if a cutover needs it.

### Local login needs break-glass access before it goes

With `LOCAL_LOGIN_ENABLED = False` and no tested emergency path, a Keycloak
outage locks out every administrator, including those who would fix Keycloak.
