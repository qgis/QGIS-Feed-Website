# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- **Enrolment pages under `/sso/manage/`**, superuser-only and linked from the
  site header.
  - The list holds the accounts that exist in the realm and where each has got
    to — created, invited, signed in, migrated — ten a page, filtered and
    searchable. Each row acts on itself: send the setup email, after one
    confirmation, or get the link. No checkbox selection, because one cannot
    survive paging.
  - *Create Keycloak accounts* opens a second page of candidates — users with
    nobody behind them in the realm — where a wave is ticked and confirmed
    against a preview of what each account would become. Not paginated, so the
    selection cannot be lost; searchable instead.
  - Accounts no invitation could reach — no address, or deactivated — are left
    off both and counted, since the admin user list is where those are dealt
    with.
  - State is read from this site's own database, so neither page waits on
    Keycloak to render; the realm is contacted only when something is pressed.
  - The existing admin actions stay and remain the way to act on a whole wave.
    All of them now call one shared engine rather than several that could drift.
- **An enrolment link can be handed over instead of emailed**, for somebody on a
  call whose email is not arriving. Shown once with a copy button, never stored,
  never logged and never put through the messages framework; the audit trail
  records that a link was issued and for whom, never the token. Needs PhaseTwo's
  magic-link extension on the realm. The link *signs the holder in*, after which
  Keycloak presents the outstanding verify-email and passkey enrolment, so it arrives
  where the email does. It is requested non-reusable and never creates a realm
  account, and a link answered for a subject other than the expected one is
  thrown away rather than handed over. It is offered **only for accounts that
  have not signed in yet**: after the first sign-in the required actions are
  spent, so a link would authenticate with no passkey at all and open a session
  across the whole realm. Somebody who has lost every device gets the email
  instead, which reaches them rather than the administrator asking for it.
- `docs/sso.md` — contributor and maintainer documentation.

### Changed

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
