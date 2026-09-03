# Single sign-on with auth.qgis.org

---

## For contributors

### Signing in

Go to `/accounts/login/` and use **Sign in with your QGIS account**. You will be
sent to `auth.qgis.org`, where you sign in with either

- a **passkey** — Touch ID, Windows Hello, a phone, or a hardware key; or
- a **password plus a one-time code** from an authenticator app.

Both end up in the same place. The passkey route has no separate code step
because a passkey already combines something you have with something you are.

### Setting your account up for the first time

You will receive an email from `noreply@qgis.org` with a setup link that is
valid for **14 days**. It walks you through verifying your address, choosing a
password and enrolling an authenticator app. All three are required.

If the link has expired, ask a feed maintainer to resend it.

### Adding a passkey

Passkeys are optional, and strongly recommended — they are faster and cannot be
phished. After completing the setup above, go to
`https://auth.qgis.org/realms/qgis/account/#/security/signing-in` and add one
under *Passwordless*. A passkey created there syncs through iCloud Keychain,
Google Password Manager or Bitwarden, so it is not tied to one device.

### "You need an invitation"

A QGIS account is not by itself an account on the feed site: this site does not
allow self-registration, and the realm is shared with the plugins and hub sites.
If you see this page, your sign-in worked but no feed account is bound to it.
Ask a feed maintainer, and tell them the QGIS account name you used.

### Losing your authenticator

Contact a feed administrator. Recovery is an administrator reset in Keycloak,
and the administrator is expected to verify who you are through a channel other
than the email address on the account.

---

## For maintainers

### Where roles are edited

**In Keycloak, on the `feed-qgis-org` client — never in Django admin.**

Django groups and the staff/superuser flags are a *projection* of the Keycloak
client roles. They are recomputed in full at every sign-in, in both directions:
a role added in Keycloak grants here, and a role removed there revokes here.
Editing the checkboxes in `/admin/` on an SSO-linked account appears to work
and is silently reverted at that person's next sign-in. The admin interface
says so beside the fields.

The mapping is `SSO_ROLE_MAP` in `settings.py`:

| Keycloak client role | Django groups | staff | superuser |
|---|---|---|---|
| `admin` | authors, approver | ✅ | ✅ |
| `web-maintainer` | authors, approver | ✅ | ✅ |
| `reviewer` | authors, approver | ✅ | — |
| `usergroup-author` | authors | ✅ | — |
| `author` | authors | ✅ | — |

Only the groups named in `SSO_MANAGED_GROUPS` are ever touched. A group created
by hand for some other purpose survives a sign-in untouched.

### Settings that matter

| Setting | Meaning |
|---|---|
| `LOCAL_LOGIN_ENABLED` | One flag, three effects: the `ModelBackend` entry, the password form on the login page, and what `/admin/login/` accepts. Set it to `False` in Phase 6. |
| `SSO_MIGRATION_LINKING` | Enables binding a token to a pre-existing local account on an exact username **and** verified-email match. Off by default; on only during the cutover window. |
| `OIDC_CREATE_USER` | Must stay `False`. The realm is LDAP-federatable, so auto-creation would grant feed accounts to the whole OSGeo directory. The backend refuses in `create_user` as well. |
| `OIDC_RP_CLIENT_SECRET` | Confidential client secret. Goes in `settings_local`, **never** in the process environment — the deployment's environment is world-readable via the Nix store and `systemctl show`. |
| `SSO_PROVISIONER_CLIENT_SECRET` | Service-account secret for the migration commands. Same rule. The web client must never hold `manage-users`. |
| `SSO_SETUP_EMAIL_ALLOWLIST` | Shell globs of addresses `sso_send_setup_links` may email. **Empty means nothing is sent.** |

### The migration commands

Four commands, deliberately separate so that each is resumable, each can be
reviewed between steps, and no single mistake is unbounded.

```
sso_export_users  →  sso_provision_keycloak  →  sso_send_setup_links  →  sso_disable_local_passwords
   report only         creates realm users        sends setup email        after confirmed login
```

Run them with `nix run .#manage -- <command>`.

**1. `sso_export_users`** — read-only. Writes a row per account with the
Keycloak username and client roles it would get, and flags the ones that need a
human decision: case collisions after lowercasing, duplicate or missing email
addresses, dormant and never-used accounts, and — most importantly —
`username-exists-in-realm`, which may mean the name belongs to somebody else.

```bash
nix run .#manage -- sso_export_users --check-realm --output migration-report.csv
```

Nothing else runs until a maintainer has read this. Commit the redacted report
as the migration record.

**2. `sso_provision_keycloak`** — dry run unless `--commit`. Creates realm users
with no credentials and the required actions set, reads back the generated
subject, and records a `KeycloakIdentity`. Flagged accounts are skipped unless
`--include-flagged`. An existing realm user with the same name is never claimed.

```bash
nix run .#manage -- sso_provision_keycloak                    # dry run
nix run .#manage -- sso_provision_keycloak --commit --limit 5
```

**3. `sso_send_setup_links`** — the one that sends real email.

> ⚠ **Staging can email real contributors.** The staging database is restored
> from production and holds every contributor's real address, and the staging
> realm uses the real Resend credential and can send from `noreply@qgis.org`.
> An unguarded run there emails the whole community a setup link pointing at a
> realm that will later be discarded.

The allowlist is the guard, and it fails closed: with `SSO_SETUP_EMAIL_ALLOWLIST`
empty the command refuses to send anything at all. Set it in `settings_local` to
the addresses the current wave is meant to reach. In production it is also what
makes "wave 1 is administrators only" a property of the code rather than of
remembering the right `--limit`.

```bash
nix run .#manage -- sso_send_setup_links                     # dry run
nix run .#manage -- sso_send_setup_links --commit --limit 5
nix run .#manage -- sso_send_setup_links --commit --resend --username alice
```

Links last 14 days. Announce the migration on the mailing list, naming the exact
sender address, *before* the first email goes out — an unexpected account-setup
email reads as phishing.

**4. `sso_disable_local_passwords`** — run late. Only touches accounts with a
**recorded successful SSO login**, not merely provisioned ones. Accounts that
linked themselves through the migration-linking path already had this done at
link time.

**`sso_local_login_report`** — run weekly. Counts local-password sign-ins and
reports how many recently-active accounts still lack a Keycloak identity, which
is condition (a) of the exit criterion for retiring local login.

### Before retiring local login

Do not set `LOCAL_LOGIN_ENABLED = False` until break-glass access (US-1.3)
exists and has been tested. Without a documented, audited, shell-only emergency
path, a Keycloak outage locks out every administrator — including the ones who
would fix Keycloak.

---

Made with 💗 by [Kartoza](https://kartoza.com) |
[Donate!](https://github.com/sponsors/kartoza) |
[GitHub](https://github.com/qgis/QGIS-Feed-Website)
