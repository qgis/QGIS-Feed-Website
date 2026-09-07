# Single sign-on with auth.qgis.org

---

## For contributors

### Signing in

Go to `/accounts/login/` and use **Sign in with your QGIS account**. You will be
sent to `auth.qgis.org`, where you sign in with your **passkey** — Touch ID,
Windows Hello, a phone, or a hardware key.

There is no password and no one-time code. A passkey already combines something
you have with something you are, so there is nothing else to type, nothing to
remember, and nothing that can be phished out of you.

### Setting your account up for the first time

You will receive an email from `noreply@qgis.org` with a setup link that is
valid for **14 days**. It asks you to verify your address and enrol a passkey.
Both are required, and once done you are returned to the feed site already
signed in — no password is ever set on the account, and there is no second
login to perform.

You need a device that can create a passkey. A passkey made on a phone or in
iCloud Keychain, Google Password Manager or Bitwarden syncs across your
devices, so it is not tied to the machine you enrolled on. Enrol a second one —
a hardware key, or a passkey on another device — at
`https://auth.qgis.org/realms/qgis/account/#/security/signing-in` under
*Passwordless*, so that losing a device does not lock you out.

If the link has expired, or you have lost every passkey, ask a feed maintainer.

### "You need an invitation"

A QGIS account is not by itself an account on the feed site: this site does not
allow self-registration, and the realm is shared with the plugins and hub sites.
If you see this page, your sign-in worked but no feed account is bound to it.
Ask a feed maintainer, and tell them the QGIS account name you used.

### Losing every passkey

Contact a feed administrator, who can resend a setup link so you can enrol a
new one. There is no password to fall back on, so the administrator is expected
to verify who you are through a channel other than the email address on the
account — enrolling a second passkey in advance is much less trouble.

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
| `SSO_PROVISIONER_CLIENT_SECRET` | Service-account secret used by provisioning. Same rule. The web client must never hold `manage-users`. |
| `SSO_REQUIRED_ACTIONS` | What Keycloak makes a new user complete. `VERIFY_EMAIL` and `webauthn-register-passwordless`: a passkey and nothing else, so no password or TOTP secret is ever created. |
| `SSO_ADMIN_ACTION_MAX_USERS` | Accounts the admin action will provision in one request (25). |
| `SSO_SETUP_REDIRECT_URI` | Where Keycloak returns somebody who has finished setting up. Points at `/oidc/authenticate/` so they arrive signed in rather than at a login form, and **must be registered as a valid redirect URI on the `feed-qgis-org` client**. |

### Everything happens in the admin

There are no management commands and no reason to open a shell on the server.
Four actions, all superuser-only and all audited, in the order you use them:

| Where | Action | What it does |
|---|---|---|
| Users | *Export the migration report for selected users* | Downloads a CSV. Changes nothing. |
| Users | *Create the Keycloak account* | Creates realm accounts. Sends no email. |
| Keycloak identities | *Send the account-setup email* | Invites them. Repeatable. |
| Keycloak identities | *Disable the local Django password* | Retires the fallback, late. |

They are separate so that no step is a side effect of another: creating an
account never emails anybody, and inviting somebody can be repeated without
touching the account.

**1. Read the report first.** The CSV gives the Keycloak username and client
roles each account would get, and flags the ones needing a human decision:
case collisions after lowercasing, duplicate or missing email addresses,
dormant and never-used accounts. Keep it as the migration record.

**2. Create the accounts.** A confirmation page lists the outcome per user
before anything is written; it is the only preview there is. Capped at
`SSO_ADMIN_ACTION_MAX_USERS` (25) per run — each account costs several
synchronous calls to Keycloak inside one request and there is no task queue
here, so migrate in waves. Flagged accounts are held back unless you tick the
box. A realm name that already exists is never claimed, and an account already
linked is never provisioned twice.

**3. Send the setup email.** Links last 14 days, and a passkey-only account has
no password to fall back on, so use this again whenever somebody misses the
window or loses every device. Each send is counted on the identity record.

> **The setup link cannot be handed over manually.** Keycloak mints the action
> token inside `execute-actions-email` and returns nothing; there is no API
> that gives it back, so it only ever leaves by email. If mail is not arriving,
> the identity's detail page links straight to that account in the Keycloak
> admin console, where the credential state is visible and the send can be
> retried against Keycloak's own SMTP settings — which are separate from
> Django's. Check the realm's mail configuration there first.

> ⚠ **Staging can email real contributors.** The staging database is restored
> from production and holds every contributor's real address, and the staging
> realm uses the real Resend credential and can send from `noreply@qgis.org`.
> Check who you have selected. A setup link pointing at a throwaway realm
> cannot be unsent.

Announce the migration on the mailing list, naming the exact sender address,
*before* the first email goes out — an unexpected account-setup email reads as
phishing.

**4. Disable local passwords** — mostly automatic. An account's password is
retired the moment it first signs in through Keycloak, because that sign-in is
the proof it is reachable; `SSO_RETIRE_PASSWORD_ON_LOGIN = False` turns that
off if a cutover needs both doors open. The action is there to backfill
accounts that signed in before this was automatic, and to check the state of a
batch.

Only accounts with a **recorded successful SSO login** are ever touched; the
rest are reported and left alone, because taking the password from somebody
who has not yet signed in through Keycloak locks them out of an account they
cannot recover. Accounts that linked themselves through the migration-linking
path already had this done at link time.

### The account menu

The site header shows the signed-in username as a dropdown: **Profile**, which
opens that person's account page at `auth.qgis.org` where they manage their own
passkeys, and **Log out**. Profile appears only for an SSO-linked account,
since a local-only account has nothing to manage there.

### Tracking progress

The user list has an **SSO account** filter. Combined with the *last login*
filter it answers the question retiring local login is gated on: which accounts
still in use have nobody behind them in the realm yet.

Local-password sign-ins are recorded as audit events — filter **SSO audit
events** by action to count them.

### Before retiring local login

Do not set `LOCAL_LOGIN_ENABLED = False` until break-glass access (US-1.3)
exists and has been tested. Without a documented, audited, shell-only emergency
path, a Keycloak outage locks out every administrator — including the ones who
would fix Keycloak.

---

Made with 💗 by [Kartoza](https://kartoza.com) |
[Donate!](https://github.com/sponsors/kartoza) |
[GitHub](https://github.com/qgis/QGIS-Feed-Website)
