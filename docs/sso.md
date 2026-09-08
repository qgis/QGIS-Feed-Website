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

Contact a feed administrator, who can resend a setup link **to your address** so
you can enrol a new one. They cannot read it out to you: once an account has
signed in, a link handed to somebody else would sign *them* in as you. There is
no password to fall back on, so the administrator is expected to verify who you
are through a channel other than the email address on the account — enrolling a
second passkey in advance is much less trouble.

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
| `SSO_MAGIC_LINK_URL` | Derived from `QGIS_AUTH_URL`, like the OIDC endpoints. Answered by PhaseTwo's magic-link extension; see *Handing over a link*. |

### The enrolment pages

Two of them, linked from the site header for superusers, because they are two
jobs.

**`/manage/sso/` — accounts that exist in the realm.** Every account with a
Keycloak identity, and where it has got to: *account created*, *invited*,
*signed in*, *migrated*. Ten rows a page, filtered by state or searched by
name. Each row acts on itself:

- **Send email** — the account-setup email, after one confirmation. It reaches a
  real contributor and cannot be unsent.
- **Get the link** — see *Handing over a link* below. No confirmation: nothing
  leaves the building until you pass it on.

**`/manage/sso/create/` — accounts that do not.** Reached from the *Create
Keycloak accounts* button. Users on this site with nobody behind them in the
realm, tick the ones you want and confirm; the confirmation lists the Keycloak
username, the roles and the outcome for each before anything is written. Capped
at `SSO_ADMIN_ACTION_MAX_USERS` (25) per run, which is about how many
synchronous calls to Keycloak fit in one request. Deliberately **not
paginated** — a selection cannot be lost by paging if there is no paging — so
use the search box on a long list.

Accounts that no invitation could reach — no email address, or deactivated —
are left off and counted in a line under the table. They are not work in
progress, and the admin user list with its **SSO account** filter is the place
to deal with them. Dormant and never-used accounts *are* offered, with the flag
shown beside them: they can be enrolled, somebody just has to decide to.

Both pages read state from this site's own database, so neither waits on
Keycloak to render. The realm is contacted only when you press something.

Selecting a whole wave and acting on it in one go is still the Django admin's
job — see below. All of it runs through the same code, so the rules and the
wording are identical wherever you start.

### The same steps in the admin

The admin actions remain, and are where a whole wave gets done at once: filter
the changelist, select, act. They and the pages call the same code, so the rules
and the wording are identical. Retiring local passwords lives only here, since
it is a one-off backfill rather than part of enrolment. All superuser-only and
all audited:

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

> **Handing the link over needs an extension.** Keycloak mints the action token
> inside `execute-actions-email` and returns nothing; its own API has no way to
> give it back. With the extension below deployed, the enrolment page can fetch
> the same link and show it to you. Without it, the identity's detail page
> links straight to that account in the Keycloak admin console, where the
> credential state is visible and the send can be retried against Keycloak's
> own SMTP settings — which are separate from Django's. Check the realm's mail
> configuration there first.

#### Handing over a link

When somebody is on a call and their email is not arriving, *Get the link* on
their row fetches a link and shows it once, with a copy button. It is never
stored, never logged and never put through the messages framework; the audit
trail records that a link was issued and for whom, not the token. Reload the
page and it is gone.

Keycloak has no endpoint that hands back a setup link, so this needs PhaseTwo's
[magic-link extension](https://github.com/p2-inc/keycloak-magic-link) deployed
into `/opt/keycloak/providers/`. It authorises with `manage-users`, which the
provisioner service account already holds; without it the button reports a 404.
Check its licence and record the Keycloak version it was validated against.

**A magic link signs the person in — it is not an action token.** Enrolment
still happens because the required actions set at provisioning are outstanding
on the account, and Keycloak presents them straight after authenticating.

> ⚠ **Verify that on a throwaway realm before relying on it.** Follow a link
> all the way through. If the required actions do not fire, the person lands
> signed in with no passkey, which is worse than the email path.

**Only for accounts that have not signed in yet.** After the first sign-in
those actions are spent, so the link authenticates straight through with no
passkey and opens a session across the whole realm, hub and plugins included.
The button disappears once an account has signed in and the view refuses the
request anyway. Somebody who has lost every device gets *Send email*: the same
link, delivered to their own address rather than to the administrator asking
for it.

Two details in the request are load-bearing. `reusable` and `force_create` both
default the wrong way for us — a reusable link is a standing credential, and
`force_create` would have this site creating realm accounts as a side effect of
asking for a link — so both are sent false. And the endpoint is keyed on the
**username** where the rest of this app is keyed on `sub`, so the returned
`user_id` is checked against the identity and the link discarded if they
differ; a username that has come to point at somebody else would otherwise sign
that person into this account.

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
