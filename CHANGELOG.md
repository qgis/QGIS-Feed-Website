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
  *Profile*, linking to the person's own account page at `auth.qgis.org`, and
  *Log out*. Profile is shown only for SSO-linked accounts.
- **Passkey-only accounts.** Provisioned users verify their address and enrol
  a passkey; no password and no TOTP secret is ever set, so there is no
  password to phish or reuse. `SSO_REQUIRED_ACTIONS` carries this.
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
