# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Move an account to a new sponsor** (US-5.4), at
  `/sso/manage/reparent/<id>/`. Revoking somebody suspends everyone they
  invited, and until now the only way to lift that was to restore the person
  who was revoked, inside a seven day window. After the window there was no way
  back at all: the row offered no action, `restore()` refuses anything not
  directly revoked, every admin field is read only, and an account that already
  exists cannot be invited again. Suspended contributors were stuck for good.
  Giving the account a new sponsor now reactivates it, together with anyone the
  same revocation suspended below it, while the person actually revoked stays
  revoked. The grace window does not apply, because this is the path that has to
  work after it closes. Refuses a sponsor inside the account's own subtree,
  which would close the chain into a loop, and refuses one whose role is too
  junior to have invited the account. Administrators and web maintainers only,
  audited with the sponsor before and after.
- **A contributor's guide at `/sso/help/`** — one page answering the questions
  people actually ask: how to get an account, what to do with an enrolment
  link, what a passkey is, how to add or remove one, what your role lets you
  do, who you may invite, and what suspension means. Open to anybody, because
  the reader who needs it most is often the one who cannot sign in. The role
  ladder and the invitation allowances are built from `SSO_ROLE_TIERS` and
  `SSO_TIER_QUOTAS`, so the page cannot drift from the rules it describes.
  Linked from the sign-in page, the sign-in failure page, the enrolment list,
  the issued link page, the profile page, and the suspension banner.
- **Keycloak single sign-on (`qgis_sso`)** — Phase 3 of the
  [SSO migration plan](SSO-Feed-Migration-Plan.md). A new namespaced,
  feed-agnostic app holding the identity binding and the audit trail.
  - `KeycloakIdentity` binds a Django user to a Keycloak `sub`, with
    `link_method` recorded from the outset so grandfathered accounts can later
    be told apart from vouched ones.
  - `QGISOIDCAuthenticationBackend` matches users on `sub` alone — never on
    email — and refuses to create an account for a realm user who has none
    here. The realm is shared with hub and plugins and is LDAP-federatable, so
    auto-creation would grant feed accounts to the whole directory.
  - ID tokens are checked for `iss`, `aud`, `azp` and `exp`, which
    `mozilla-django-oidc` does not verify itself. A token minted for another
    client in the same realm is rejected.
  - Declarative role mirroring via `SSO_ROLE_MAP` / `SSO_MANAGED_GROUPS`, with
    full reconciliation at every sign-in so that revoking a role in Keycloak
    revokes it here.
  - Bounded migration linking behind `SSO_MIGRATION_LINKING`, requiring an
    exact match on username *and* verified email, and making the local
    password unusable on success.
  - RP-initiated logout and `SessionRefresh`, so signing out really ends the
    Keycloak session and a revocation there takes effect within minutes.
  - A rate-limited OIDC callback and a friendly "you need an invitation" page.
- **Account migration from the admin** — Phase 4, with no management commands
  and no shell access required. Four superuser-only actions, each audited and
  each independent of the others: export the migration report as CSV, create
  the Keycloak accounts, send the account-setup email, and disable the local
  Django password. Creating an account sends nothing, and sending can be
  repeated whenever a link expires.
  - Provisioning shows a confirmation page with the outcome per user before
    anything is written, is capped by `SSO_ADMIN_ACTION_MAX_USERS`, holds back
    flagged accounts, and never claims a realm name that already exists.
  - Disabling a password requires a *recorded successful SSO login*, not
    merely having been provisioned.
  - The user list gains an **SSO account** filter for tracking progress.
  - An identity's detail page links to that account in the Keycloak admin
    console. The setup link itself cannot be retrieved — Keycloak mints the
    token inside `execute-actions-email` and returns nothing — so the console
    is the fallback when mail does not arrive.
- **Local passwords retire themselves** on an account's first successful SSO
  login, closing the window in which both ways in worked. Controlled by
  `SSO_RETIRE_PASSWORD_ON_LOGIN`.
- **Account menu in the site header** — the username opens a dropdown with
  *Profile* and *Log out*.
- **Profile page at `/sso/profile/`** — the account as this site sees it, and
  the person's passkeys with the date each was enrolled. *Add a passkey* and
  *Remove* start a sign-in carrying `kc_action`, so Keycloak runs the action and
  returns them here: WebAuthn binds a credential to the relying party that
  created it, so no other site can register or delete one, and this site
  therefore never changes a credential itself — it reads the list and nothing
  more. The action is put in the session by a view that has already checked it,
  so a crafted link cannot choose one. Removing the last passkey is refused,
  because these accounts have no password and the setup link an administrator
  could send is refused for anybody who has already signed in.
- **Passkey-only accounts.** Provisioned users verify their address and enrol
  a passkey; no password and no TOTP secret is ever set, so there is no
  password to phish or reuse. `SSO_REQUIRED_ACTIONS` carries this.
- **An enrolment link can be handed over instead of emailed**, for somebody on a
  call whose email is not arriving. It gets a page of its own at
  `/sso/manage/link/`, showing that one link and nothing else: a copy button, and
  a QR code below it for scanning the link straight onto the device the passkey
  will live on instead of reading a long token out. The QR is rendered in-process
  as inline SVG with `segno`, so the credential never reaches disk or a
  third-party image service, and the copyable field stays the primary route
  because a QR is reachable by neither keyboard nor screen reader. Shown once,
  never stored, never logged and never put through the messages framework; the
  audit trail records that a link was issued and for whom, never the token. The
  page takes the link out of the session as it renders, so a reload offers to
  issue a fresh one rather than showing the old one again. Needs PhaseTwo's
  magic-link extension on the realm. The link *signs the holder in*, after which
  Keycloak presents the outstanding verify-email and passkey enrolment, so it arrives
  where the email does. It is requested non-reusable and never creates a realm
  account, and a link answered for a subject other than the expected one is
  thrown away rather than handed over. It is offered **only for accounts that
  have not signed in yet**: after the first sign-in the required actions are
  spent, so a link would authenticate with no passkey at all and open a session
  across the whole realm. Somebody who has lost every device gets the email
  instead, which reaches them rather than the administrator asking for it.
- **Invitations — the first slice of the web of trust.** Somebody with quota
  brings in somebody they know, and the account that results permanently records
  who vouched for it.
  - `/sso/manage/` opens to anyone with a realm account. A superuser sees every
    account as before; everybody else sees only the ones they vouched for, and
    is refused a row outside that set even by posting its id.
  - An **Invite** menu replaces *Create Keycloak accounts*. *New user* is open
    to anyone whose roles carry quota and creates the account in both places
    straight away; *Existing user* stays superuser-only, because it reaches the
    whole user list.
  - Inviting lands on `/sso/manage/link/` with the setup link, shown once,
    carried there in the session rather than through the messages framework,
    whose fallback storage is a cookie. Nothing is emailed until you press
    *Send email*.
  - Who may offer what comes from `SSO_ROLE_TIERS` and `SSO_TIER_QUOTAS`,
    declared beside `SSO_ROLE_MAP` and reusing its role names. Holding two roles
    gives the more privileged tier and the larger allowance, never the sum. A
    place is held by every account you invited that has not signed in yet. Tier
    governs invitations only; content permissions are unchanged.
  - The role offered is checked when the form is rendered and again on submit,
    and username and address must be free both here and in the realm.
  - `KeycloakIdentity` gains `sponsor` and `is_root`, with a check constraint
    that every identity has a sponsor unless it is a root — or was grandfathered
    in before there was a graph. Migrated accounts are **not** given an invented
    sponsor: US-9.4 asks for them to stay an explicit untrusted-by-default
    cohort, and `link_method` is what tells them apart.
  - There is no self-service redemption endpoint, deliberately. Creating the
    account when the invitation is issued means the inviter types the address,
    so a leaked link cannot be used by a stranger to make a realm account.
- **Withdrawing trust.** Revoking somebody removes their Keycloak client roles,
  ends their sessions and disables their realm account, so the block holds at
  `auth.qgis.org` rather than depending on this site. Everybody they vouched for
  is *suspended*: still able to sign in, shown a banner explaining why, and
  holding no permissions until it is reversed.
  - Suspension is enforced through role mirroring, which grants nothing while it
    lasts. Removing Django groups alone would not have worked — mirroring
    reconciles them from the token at every sign-in and would hand them back.
  - You may act anywhere in your own subtree and nowhere else; a root may act
    anywhere except on another root, since removing one needs a second root to
    agree (US-5.5, not built).
  - The blast radius is listed by name before you confirm, and a reason is
    required and audited.
  - Reversal restores the recorded roles and clears the subtree in one action
    within `SSO_REVOCATION_GRACE_DAYS`; accounts suspended by a different
    revocation are left alone.
  - Content is retained. `SSO_ON_REVOKE` names what the site does about
    unpublished work; the feed's returns pending and approved entries to draft so
    revoked work cannot be published by a reviewer who has no reason to know.
- **The enrolment list shows who vouched for whom**, which the sponsor field has
  recorded since invitations were added without anything displaying it.
- `docs/sso.md` — contributor and maintainer documentation.

### Changed

- **The SSO documentation splits in two, and gets diagrams.** `docs/sso.md` is
  now the guide you read while doing the job: what each page does, who may act,
  and how to migrate a wave. It carries a sign-in sequence diagram, the account
  state ladder, and a picture of what a revocation reaches. The reasoning behind
  each rule moves to `docs/sso-design.md`, linked from the top. The maintainer
  page loses a quarter of its words, and the state filter names it described
  ("account created", "invited", "signed in", "migrated") are corrected to the
  ones the code uses.
- **The SSO pages say what they mean.** Words that came from the
  implementation no longer reach a reader: "realm", "Keycloak username" and
  "subtree" are gone, roles read as *Administrator* and *User group author*
  rather than `admin` and `usergroup-author`, and the profile page says what
  your account lets you do instead of naming a Django group. `Login` and
  `Log Out` become `Sign in` and `Sign out`, and the enrolment list is now
  `People`. Error messages say what to do next, and technical detail moves out
  of the message body into the logs. New settings `SSO_ROLE_LABELS`,
  `SSO_ROLE_SUMMARIES` and `SSO_GROUP_LABELS` hold the names.
- **The account state ladder now means one thing.** *Created → Link sent →
  Active*, plus *Suspended* and *Revoked*. `Invited` was ambiguous once accounts
  could arrive by invitation — it described both a state and an origin — and
  `Migrated` was not a state at all: it meant a local password had been retired,
  which can only happen to a grandfathered account and says nothing about
  progress. That is a marker on the row now.

- **Login page** keeps its existing layout — username, password and the QGIS
  account button. The only change is that the password form is now rendered
  only while `LOCAL_LOGIN_ENABLED` is set.
- **`LOCAL_LOGIN_ENABLED`** gates the `ModelBackend` entry, the password form
  and `/admin/login/` together. Django admin's own login view now redirects to
  the site login page, so one page enforces the policy.
- **`/accounts/login/` no longer loops for a signed-in user.** `qgisfeed`
  guards its views with `permission_required`, which redirects to the login
  page; for somebody already authenticated that meant a login form above a
  header offering logout. It now explains that the account lacks the
  permission, and that permissions follow the roles on the QGIS account. Rare
  before, since every staff account was in the authors group; ordinary now
  that roles are mirrored. Reached without a `next` — which is where Keycloak
  returns somebody straight after they enrol a passkey — a signed-in visitor is
  sent on to the site instead of being shown the form again, which had them
  signing in twice to get in once.
- **`SessionRefresh` leaves the login, admin-login and sign-in-failed pages
  alone** (`OIDC_EXEMPT_URLS`). Those are the pages a user reaches *because*
  their session is in doubt, so renewing a token from them is a round trip
  nobody asked for, and on the login page a loop.
- **Provisioning names the accounts it holds back.** Confirming a selection and
  being told only that there was nothing to do gave no way to see which account
  was skipped, or why, without previewing again.
- **`qgisfeed.signals.setup_group`** now only considers the user being saved,
  and leaves SSO-linked accounts alone. It previously swept every staff user on
  *any* user save, which undid role revocations — including via the
  `last_login` write that happens immediately after authentication.
- **The feed list's pager moved into `layouts/pagination.html`** so the SSO pages
  use the same one rather than a copy of it. Same appearance; it now carries the
  current filters through with Django's `{% querystring %}` instead of a
  hand-built query string that dropped any parameter nobody remembered to add,
  and it is anchors rather than buttons driving `window.location`, so paging
  works with JavaScript off and from the keyboard.
- `tests` in the dev shell and the `django-tests` flake check run `qgis_sso`
  alongside `qgisfeed`.

### Fixed

- **An administrator could revoke the administrator who invited them, and
  suspend themselves doing it.** `may_revoke()` promised "never on an ancestor"
  but never checked: `if actor.is_superuser and effective_tier(actor) == 0`
  returned early, so the ancestor test on the last line was unreachable for
  tier 0. The only remaining guard read `is_root`, which defaults to `False`
  and is set nowhere outside the tests, so in a real deployment it never fired.
  Because the actor sits inside the target's subtree, the cascade suspended the
  actor: the target ended revoked, the actor suspended, and neither could undo
  it, since `restore()` requires an active actor. Authority is now refused
  upwards and between administrators, target tiers are read from the stored
  roles so a suspended administrator still counts as one, and `preview()`
  refuses outright if the actor appears in the blast radius. Restoring keeps
  the old reach rule, so an administrator revoked before this fix can still be
  brought back.

### Security

- `OIDC_CREATE_USER` is pinned to `False` and `create_user()` raises
  regardless, so flipping the setting alone cannot open the site to the shared
  realm.
- `OIDC_RP_CLIENT_SECRET` and `SSO_PROVISIONER_CLIENT_SECRET` are read from
  `settings_local` and never from the process environment, which is
  world-readable through the Nix store and `systemctl show`.
- Local-password sign-ins are logged at `WARNING` and audited, so the exit
  condition for retiring the fallback can be measured rather than assumed.
- **Flash messages are no longer rendered with `|safe`.** Every message on the
  site was passed through unescaped. None of them carried HTML, but the new
  enrolment page reports on accounts by name and quotes the identity provider's
  error text, either of which would have been an injection point.
