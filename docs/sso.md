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

`/sso/manage/` is open to anyone with a realm account, and shows what each is
entitled to see: a superuser gets every account with a Keycloak identity,
everybody else only the ones they vouched for. Ten rows a page, filtered by
state — *account created*, *invited*, *signed in*, *migrated* — or searched by
name. Each row acts on itself:

- **Send email** — the account-setup email, after one confirmation. It reaches a
  real contributor and cannot be unsent.
- **Get the link** — see *Handing over a link* below. No confirmation: nothing
  leaves the building until you pass it on.

Both are refused for a row outside your own branch, whether or not the listing
showed it.

The **Invite** menu is described under *Invitations* below. *Existing user*
opens `/sso/manage/invite/existing/`: accounts on this site with nobody behind
them in the realm, ticked and confirmed against a preview of the Keycloak
username, roles and outcome for each. Capped at `SSO_ADMIN_ACTION_MAX_USERS`
(25) per run, which is about how many synchronous calls to Keycloak fit in one
request, and deliberately **not paginated** — a selection cannot be lost by
paging if there is no paging.

Accounts no invitation could reach — no email address, or deactivated — are left
off and counted in a line under the table. They are not work in progress, and the
admin user list with its **SSO account** filter is where those get dealt with.
Dormant and never-used accounts *are* offered, with the flag shown beside them.

The pages read state from this site's own database, so neither waits on Keycloak
to render. The realm is contacted only when you press something.

Selecting a whole wave and acting on it in one go is still the Django admin's
job — see below. All of it runs through the same code, so the rules and the
wording are identical wherever you start.

### Linking an account that already exists in the realm

The realm is shared with hub and plugins, so a long-standing contributor here
very likely already has a QGIS account. *Invite existing user* now offers a
third outcome for those: **will be linked**, naming the QGIS account it matched.
Nothing is created in the realm; the existing account is bound to the one here
and given the client roles this account's current Django state implies.

**Matched on an exact address that Keycloak reports as verified, and nothing
else.** A username is not evidence — it can belong to somebody else entirely,
which is why claiming one was refused outright before this existed. An address
that differs, or one the realm has not verified, is refused with which of the
two it was, because they need different fixing. So is a realm account already
bound to somebody here: two local accounts on one subject would break every
lookup in the app.

> **Superuser here comes from the `admin` client role on `feed-qgis-org`, never
> from a realm role.** It is tempting to reach for a realm-wide role like
> `qgis-superuser` and read it at sign-in — it would be less work. It would also
> mean whoever holds that role for hub or plugins becomes a superuser here,
> which is exactly what the client-scoped design exists to prevent, and what
> US-9.2 asks for a test against. `proposed_roles` derives the client role from
> what the account can already do here, so linking grants no privilege that was
> not already held.

Role mirroring then does the rest: at their next sign-in `mirror_roles`
reconciles Django's groups and flags from the token in full, granting and
revoking, so removing the role in Keycloak takes it away here too.

### Withdrawing trust

**Revoked** is what happens to the person acted on. Their client roles are
removed, their sessions ended and their realm account disabled, so the block
holds at `auth.qgis.org` rather than depending on this site being consulted.
Locally they are deactivated, and the roles they held are recorded first so a
reversal has something to put back.

**Suspended** is what happens to everybody they vouched for. Those people can
still sign in and see a banner explaining why, but hold no permissions — US-5.2
is explicit that suspension is lighter than revocation, because they have done
nothing wrong.

That distinction is enforced in different places, and it matters. Suspension is
local, because `roles.mirror_roles` reconciles Django's groups from the token at
**every** sign-in: taking somebody's groups away achieves nothing on its own, as
the next authentication hands them straight back. Anything meant to last happens
in Keycloak, or is respected by the mirroring.

Who may act:

- Anywhere in your own subtree, and nowhere else. Never an ancestor, never
  across branches.
- A root may act anywhere except on another root. Removing a root needs a second
  root to agree (US-5.5), which is not built, so it is refused with that reason.
- Standing down voluntarily (US-5.3) is not built either; it re-parents
  descendants rather than suspending them, so it is not the same button.

Withdrawing trust has its own page, `/sso/manage/revoke/<id>/`, reached from
the row. Getting there changes nothing, so it can be opened, read and left. It
lists the whole blast radius **by name** — US-5.2 asks for no surprises, and a
cascade larger than expected is the one mistake here that cannot be walked back
casually — and requires a reason, which goes in the audit trail. Being able to
see the page is the same check as being able to act on it, and a forbidden
account reads exactly like one that does not exist.

**Reversal** restores the recorded roles, re-enables the realm account and
clears the subtree in one action, within `SSO_REVOCATION_GRACE_DAYS` (7). After
that the record stays but the button goes: reversing months later is a
re-invitation, not an undo. Accounts suspended by a *different* revocation are
left alone.

Content is **retained**. `SSO_ON_REVOKE` names what this site does about the
person's unpublished work — `qgisfeed.trust.on_revoke` returns entries in
*pending review* or *approved* to *draft*, so an approved entry from somebody
revoked an hour ago cannot be published by a reviewer working through the queue
who has no reason to know. Published entries are left alone, and nothing is
deleted or re-attributed.

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

### Invitations

The first slice of the [web of trust](../SSO-Web-Of-Trust.md): somebody with
quota brings in somebody they know, and the account that results permanently
records who vouched for it.

`/sso/manage/` is open to anyone with a realm account, and shows what each is
entitled to see — a superuser gets every account, everybody else only the ones
they vouched for. The **Invite** menu offers two routes:

- **New user** — anyone whose roles carry quota. A short form, and the account
  is created immediately in both places. Nothing is sent until you send it.
- **Existing user** — superusers only. Grafts accounts that already exist here
  into the realm; it reaches the whole user list, which is why it is not open
  to everybody with quota.

Who may offer what is the ladder in `SSO_ROLE_TIERS`, whose role names are the
ones already in `SSO_ROLE_MAP`:

| Tier | Role | May offer | Open places |
|---|---|---|---|
| 0 | `admin` | anything | unlimited |
| 1 | `web-maintainer` | tier 1 and below | 25 |
| 2 | `reviewer` | tier 2 and below | 10 |
| 3 | `usergroup-author` | tier 3 and below | 10 |
| 4 | `author` | `author` only | 3 |

Somebody holding two roles gets the more privileged tier and the *larger* of the
two allowances, never their sum. A place is held by every account you invited
that has not signed in yet, and frees up when they do. Tier governs invitations
and nothing else — what you may do to an entry still comes from the mirrored
Django permissions.

The role is checked against the inviter's tier when the form is rendered *and*
again on submit, because a form is only a suggestion. Username and address must
be free both here and in the realm; an address already in the realm belongs to
somebody, and enrolling a second account onto it would send them a setup link
they never asked for.

After inviting, the setup link is shown once on `/sso/manage/`, with a copy
button, exactly as *Get the link* shows it. It is carried there in the session
rather than through the messages framework, whose fallback storage is a cookie.
Reload and it is gone; use **Send email** on the row to have Keycloak deliver it
instead.

**There is no self-service redemption endpoint**, deliberately. An earlier
design handed out a signed token that created the account when redeemed, which
meant a public page where a stranger holding a leaked link could create a realm
account with an address of their choosing. Creating the account at the moment of
invitation removes that entirely: the inviter types the address.

### The account menu and the profile page

The site header shows the signed-in username as a dropdown: **Profile** and
**Log out**.

Profile is `/sso/profile/` on this site. It shows the account — username,
address, the groups the Keycloak roles have granted — and lists that person's
passkeys with the date each was enrolled.

**Adding or removing one happens at `auth.qgis.org`, and has to.** WebAuthn
binds a credential to the relying party that created it, so no other site can
register or delete a passkey for the realm; a browser will refuse. Pressing
*Add a passkey* therefore starts an ordinary sign-in carrying `kc_action`, an
[application-initiated action](https://www.keycloak.org/docs/latest/server_admin/#con-aia_server_administration_guide):
Keycloak runs the action, then returns the user to the profile page.

Two consequences worth knowing:

- **This site never changes a credential.** It reads the list through the
  provisioner service account and nothing more; every change is authorised by
  the user at Keycloak. The action is put in the session by a view that has
  already checked it, so a crafted link cannot ask Keycloak to run an action of
  its own choosing.
- **The last passkey cannot be removed from here.** These accounts have no
  password, and the setup link an administrator could send is refused for
  anybody who has already signed in, so removing the only one is a lockout with
  no way back. Enrol the replacement first.

A local-only account gets the page too, and is told it has no passkeys to
manage. *Everything else, at auth.qgis.org* links to the full account console.

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
