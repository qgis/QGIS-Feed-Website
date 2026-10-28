# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Signing in with a local password works again.** The sign in page holds two
  forms, one for each way in, and the QGIS form stayed open around the password
  fields. HTML has no nested forms, so the browser dropped the inner one and the
  username, the password and the Sign in button all belonged to the outer form.
  Pressing Sign in sent a GET to the account service, which answered with the
  Keycloak sign in page, so a local password could never be tried. The QGIS form
  closes after its own button now. A test parses the page and asserts which form
  will submit each control, because a string search through the markup cannot see
  this.

- **A username the QGIS account service would refuse is now refused on the
  invite form, and the message says why.** The form only checked that a name was
  not empty and not taken. A name with a space in it, or one shorter than three
  characters, passed here and was then refused at auth.qgis.org, so the reader
  was told the service could not create the account just now. That was untrue and
  gave them nothing to fix. The rules Keycloak applies now live in
  `qgis_sso.validation` and the form applies them first: three to 255
  characters, no spaces, and none of `< > & " '`, for the username and for both
  names. The username is saved in lower case, which is how Keycloak stores it, so
  the account here and the QGIS account carry one name instead of two that differ
  by case. One rule stays with the realm, the one about names mixing scripts to
  read as another name, because it depends on tables that change with the Unicode
  version.
- **The role on the invite form is no longer chosen for you.** The dropdown had
  no empty option, so the browser preselected the first one, which is the most
  privileged role the inviter may offer. An administrator who never opened the
  dropdown created another administrator. It now starts on "Choose a role", and
  the form says so if it is submitted without one.

- **An invitation no longer refuses its own holder when Keycloak sits behind
  two addresses.** `QGIS_AUTH_URL` was doing two jobs: the public address that
  browsers use and tokens come from, and the address this site calls the admin
  API on. Where those differ, for example an admin API reachable only through a
  VPN, the only way to create an invitation was to point `QGIS_AUTH_URL` at the
  internal address. The new account then recorded that address as its issuer,
  and the first sign-in refused it, because the token said the public one. The
  two jobs are two settings now: `QGIS_AUTH_URL` stays public, and
  `SSO_KEYCLOAK_SERVER_URL` says where the admin API is, defaulting to
  `QGIS_AUTH_URL`. Provisioning records the issuer the request path asserts
  rather than the address it called, so the two can no longer drift. The
  refusal also logs both issuers, since the message cannot carry them. An
  account already created against the wrong address keeps it and has to be
  invited again.

### Changed

- **Every confirmation is a page of its own now.** Sending somebody their setup
  link and cancelling an invitation used to ask in a panel above the people
  table, and creating accounts for a wave of existing users asked above the
  candidate list. A confirmation on a busy page is scrolled past, it is lost on a
  refresh, and it has no address to come back to. Sending the email now lives at
  `/sso/manage/send-email/<id>/` and cancelling at
  `/sso/manage/cancel-invitation/<id>/`, which is how withdrawing trust and the
  two sponsorship pages already worked. The wave preview keeps its post, because
  the selection lives in that form, but it renders as a page with nothing behind
  it. Each row action applies the same rule to a typed URL that the list applied
  to the row, and the filter and the page you were on travel to the confirmation
  and back.
- **The people page costs the same however large the site gets.** It used to
  read every account in the database on every page load, whichever page you
  asked for: it built a row for everybody, then threw away the ones with no
  QGIS account, then searched, filtered and sorted that list in Python. Paging
  saved the rendering and nothing else. The filter, the search, the order and
  the page are all one query now, and the state each row shows is a `CASE` the
  database evaluates, with a test that compares it against the Python version
  state for state. The trust graph is read in one recursive query for the whole
  page as well, where a page of ten used to walk it twenty times, once upwards
  from each row and once upwards from the person looking, a round trip per
  level of the tree. **State** now sorts as well, in the order people move
  through it rather than alphabetically.

- **The migration report no longer warns about clashing names and shared
  addresses.** Those two warnings needed every account compared against every
  other one, which is the only reason the enrolment pages read the whole user
  table to show ten rows. Neither condition can arise: the invite form already
  refuses a username or an address that is taken here, whatever the case, and
  asks QGIS accounts as well, and the site has no self registration. Neither
  was what kept provisioning safe either. A clashing name is still refused when
  we look it up in the realm, a shared address is still refused when it resolves
  to an account that already belongs to somebody here, and both say so in words.
  The warnings only moved that news from the run to the preview, for something
  that is not there. The report still flags a missing address, a deactivated
  account, and accounts that are dormant or have never been used.

- **The people page shows the date of invitation, and the headers sort it.**
  The list opens with the newest invitation first, and **Account**, **Date of
  invitation** and, for a superuser, **Invited by** sort the whole list rather
  than the ten rows in front of you. Sorting keeps whatever you searched for or
  filtered by, and finishing an action on a row brings you back to the same
  order. Two columns changed to make room. **Last step** is gone: it repeated
  what the **State** column already said, and the date beside somebody is now
  the day they were invited. **Invited by** only appears for a superuser,
  because everybody else sees only the accounts they vouched for and was
  reading their own name on every row. The small **old password switched off**
  note went with the last step column; it is still on the account in the Django
  admin.

- **Invitation limits count the invitations you have open, not the ones you
  have ever sent.** That was always the intent, and `tiers.remaining()` already
  subtracted the people who had signed in. In practice it still behaved like a
  lifetime cap, because nothing else ever gave a place back: a mistyped
  address, somebody who changed their mind, or an invitation that was simply
  never taken up held one of your places for good. An author with three places
  could be out of invitations after three invitations, for ever. Two things
  free a place now. An inviter can **cancel an invitation** from the row on the
  people page, which removes everything the invitation created: the Keycloak
  account, the identity record and the Django user. A place stands for a real
  account in the shared realm, so giving the place back has to give back what it
  took, or the count is a number the inviter can reset at will. Nothing is kept
  but the audit record, which keeps the username after the account is gone, so
  the same person can be invited again with the same name and address.
  Cancelling reaches only invitations made through Invite → New user, where
  every record was ours to create, and only before anybody has signed in. And a
  place held by an account whose trust has been withdrawn stops counting,
  because nobody could ever use it. Anybody may now invite as many people as
  they need over time, as long as they are not all waiting at once. What this
  bounds is the live, unused accounts one sponsor holds at a time, not how many
  they create over the years. The copy on the invite form, the people page and
  the contributor guide says all of it, and the tier 4 lifetime cap in the
  design document, which was never built, is gone.
- **Each row offers only the buttons that fit the account.** The setup email and
  the enrolment link are for somebody who has not arrived, so they are gone once
  an account has signed in, and posting either is refused. Withdraw trust is for
  somebody who has, so it is gone before then, with `revocation.refusal` saying
  to cancel the invitation instead: an account with a history is revoked, an
  invitation nobody took up is cancelled.

### Added

- **Offer to sponsor a suspended colleague** (US-5.6), at
  `/sso/manage/sponsor/<id>/`. Until now a cascade could only be lifted by an
  administrator or a web maintainer, so a contributor whose sponsor lost their
  access waited for a maintainer to notice. Anybody who holds every role the
  suspended account holds can now offer to become their sponsor. Rank grants
  nothing here: an author can take on an author, a reviewer can take on an
  author or a reviewer, and nobody can take on somebody more senior, so the tier
  ladder still cannot be climbed from below. Nothing changes until the suspended
  person accepts. They read the offer and the reason on their own account page
  and can decline it; the sponsor can withdraw it until then. Several people may
  offer at once, and accepting one withdraws the rest. Accepting re-parents the
  account and brings back anyone the same revocation suspended below it, exactly
  as moving somebody does. We check the sponsor's standing again at acceptance,
  so an offer from somebody since demoted or revoked is refused rather than
  honoured. Only an account suspended by somebody else's revocation can be taken
  on: a person revoked in their own right still needs a maintainer. Every step
  is audited.
- **Find a suspended colleague by their full username.** A contributor who is
  not an administrator sees only the accounts they invited, which left them
  unable to make the offer above. Typing a username in full now also reaches one
  suspended account outside their own branch. Part of a name searches only their
  own branch, an email address never reaches outside it, and the row a full name
  returns shows the username without the email address. Browsing every suspended
  account is still not possible: that would hand every contributor a directory
  of who is in trouble.
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

- **Inviting a wave of people is faster, and the page says it is working.**
  Deciding about somebody is a read against auth.qgis.org, and a wave made
  those reads one after another while the administrator watched a page that had
  not changed. They now run together, up to eight at a time, with the answers
  still in the order the people were picked. The feed client's UUID and role
  list are cached for ten minutes instead of being fetched twice on every
  button press, and resolving the publish permission once for the whole
  selection removes two database queries per person. Every form on the site now
  shows its button working and refuses a second press while the first is in
  flight, which also closes a hole where double clicking "Create QGIS accounts"
  sent the run twice. The invite pages cover the list with a short note saying
  what is happening and why it takes a moment.
- **The profile page paints before it has heard from auth.qgis.org.** Listing
  somebody's passkeys is two round trips, and the page used to hold everything
  for them, so a slow account service delayed the name and permissions that
  were already to hand. The list now arrives on its own straight afterwards.
  Read only, answered from the signed in account, and never from anything in
  the URL.
- **Reads against the account service give up sooner and retry once.** A lookup
  now waits five seconds to connect and ten to be answered, rather than thirty,
  so a page recovers with a message instead of sitting there. A GET that fails
  on the way out, or comes back from a tired gateway, is retried twice. Writes
  are never retried: creating an account twice is worse than reporting that it
  failed.
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
- `nix run .#test` and the Nix integration check run `qgis_sso` alongside
  `qgisfeed`.
- The Python environment skips `sentry-sdk`'s own test suite, one case of which
  fails inside the Nix sandbox and took every `nix build` down with it. Nothing
  about this project is under test there.

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
- **A missing GeoIP database no longer takes the site down.** `GeoIP2()` was
  constructed outside the `try` that guards the lookup in both
  `qgisfeed/signals.py` and `qgisfeed/utils.py`, so an absent or unreadable
  `GEOIP_PATH` raised on every user visit rather than degrading to no location.
- **The CI GeoIP fixture is pinned to something immutable.** The `integration`
  check fetched `GeoLite2-City.mmdb` from the P3TERX mirror's
  `releases/latest/download` URL, which is re-resolved to new content whenever
  MaxMind publish, breaking the build on a hash mismatch; older releases there
  are deleted, so pinning a dated tag was not a way out either. It now fetches
  MaxMind's own dual Apache-2.0/MIT `GeoIP2-City-Test.mmdb` pinned by commit
  SHA, which can neither change nor disappear. The geolocation assertions and
  the two `spatial_filter` fixture polygons move from Indonesia to London
  accordingly. How production obtains its database is unchanged.

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
