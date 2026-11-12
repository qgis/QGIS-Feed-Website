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

### The people pages read local state

We build both listings from this site's own database. Nothing waits on Keycloak
to render, so an outage there does not take the page down. We contact the realm
only when you press something.

### The people list is answered by the database

Every question the list asks is a queryset: who you may see, the search, the
state filter, the order and the page. The state is a `CASE` expression that
mirrors `state_of` exactly, and a test compares the two state for state,
because a filter that disagreed with the tag beside it would be worse than no
filter.

We read the trust chains for a whole page in one recursive query, so deciding
which rows may offer *Withdraw trust* costs no query per row.

### The invite page is capped, and has no paging

Each account costs us several calls to Keycloak inside one request, and there
is no task queue here, so `SSO_ADMIN_ACTION_MAX_USERS` caps what one run will
do. We left paging out on purpose. You cannot lose a selection by paging if
there is no paging.

### Linking matches on a verified address and nothing else

A username can belong to somebody else, so it proves nothing. We refuse an
address that differs and an address the realm has not verified. The page says
which one it was, because you fix them in different ways.

We also refuse a realm account that is already linked to somebody here. Two
local accounts on one subject would break every lookup in the app.

`proposed_roles` works out the client role from what the account can already do
here, so linking grants no new privilege.

### We create the account when you invite

A link that created the account when somebody used it would give a stranger
with a leaked link a public way to make realm accounts. We create the account
at the moment of invitation instead, so the inviter types the address.

We also refuse an address the realm already knows. That address belongs to
somebody, and a second account on it would send them a setup link they never
asked for.

### We check tiers twice

We check the role on offer against the inviter's tier when we render the form,
and again when they submit it, because a form is only a suggestion. Tier
governs invitations and nothing else. What somebody may do to a feed entry
comes from the mirrored Django permissions.

Quota counts the invitations still open, not every account somebody ever
invited, so a sponsor is not punished for people who arrived and got to work.
Three things end an open invitation: the person signs in, the sponsor cancels
it, or trust in the account is withdrawn.

### Cancelling an invitation undoes it

An invitation is an account, created in the shared realm at the moment of
invitation. Anything short of removing it would leave the name and the address
taken. So cancelling removes the realm account, the identity and the local
user. The place comes back, and the same person can be invited again with the
same name and address. `SsoAuditEvent` keeps the username and lets its user go
null, so the record of who ended the account survives it.

The local deletes and the realm delete run in one transaction, with the realm
last. If the realm refuses, the transaction rolls back and nothing changes. If
something on this site still refers to the account, the local delete fails
first and the realm account stays too.

Cancelling is only for an account the invite form made, and only before anybody
has used it. The invite form is the one place that writes `link_method` as
`invitation`. Somebody who has signed in has a history, so their invitation can
no longer be cancelled. Withdraw trust from them instead.

The quota bounds how many unused accounts one sponsor holds at a time. It does
not bound how many accounts somebody creates over the years, because every
account names who vouched for it, and revoking a sponsor suspends everybody
they brought in.

## Withdrawing trust

### Revocation reaches Keycloak, suspension stays local

`mirror_roles` rebuilds Django's groups from the token at every sign-in, so
taking somebody's groups away here would last only until their next sign-in.

Revocation disables the realm account, so the block holds at `auth.qgis.org`
and does not depend on anybody asking this site. Suspension can stay local,
because mirroring already reduces a suspended account to no permissions. It is
the lighter of the two, because the people it reaches have done nothing wrong.

We record somebody's roles before we remove them, so a restore has something to
put back.

### Administrators follow the same subtree rule as everybody else

An administrator can only revoke accounts below them in the tree. Otherwise an
administrator could revoke whoever invited them, and be suspended by their own
cascade.

### An administrator cannot revoke another administrator

Removing one should need a second administrator to agree, and we have not built
that. So we refuse the case rather than allow half of it. To remove an
administrator, take the `admin` role off them in Keycloak first, then revoke.

### The revoke page names everybody it will reach

A cascade larger than you expected is hard to walk back, so we name every
affected person before you act, and we ask for a reason for the audit trail.
Opening the page changes nothing. Seeing the page runs the same permission
check as acting on it, and an account you may not touch looks exactly like one
that does not exist, so nobody can use the page to map the tree.

### The grace window closes

Within `SSO_REVOCATION_GRACE_DAYS`, one action puts the roles back, re-enables
the realm account and clears the suspension across the subtree. After that the
record stays but the button goes, because reversing a revocation months later
is a new invitation rather than an undo.

### We keep the content, and we do not leave unpublished work live

`SSO_ON_REVOKE` says what this site does about a revoked person's work, and
`qgis_sso` itself knows nothing about entries or statuses.
`qgisfeed.trust.on_revoke` sends entries in *pending review* or *approved* back
to *draft*. Otherwise a reviewer working through the queue could publish an
entry from somebody revoked an hour ago. Published entries stay published, and
we delete nothing and re-attribute nothing.

## Setup links

### Keycloak will not hand back a setup link

Keycloak mints the action token inside `execute-actions-email` and returns
nothing, and its own API gives us no way to ask for it back. So showing a link
needs PhaseTwo's
[magic-link extension](https://github.com/p2-inc/keycloak-magic-link),
authorised with `manage-users`, which our provisioner service account already
holds.

### A magic link signs the person in

It is not an action token. Enrolment still happens because the required actions
we set at provisioning are still outstanding on the account, and Keycloak
presents them as soon as it has authenticated somebody. That is also why we
offer the link only before the first sign-in. After it, those actions are
spent, so the link authenticates straight through without asking for a passkey
and opens a session across the whole realm, hub and plugins included.

Follow a link all the way through on a throwaway realm before you rely on this.
If the required actions do not fire, the person lands signed in with no
passkey, which is worse than the email path.

### We send `reusable` and `force_create` as false

Both of them default the wrong way for us. A reusable link is a standing
credential, and `force_create` would have this site creating realm accounts as
a side effect of asking for a link.

### We check the returned `user_id` against the identity

The magic-link endpoint works from the username, where the rest of this app
works from `sub`. If a username has come to point at somebody else, the link
would sign that person into this account, so we throw the link away when the
two do not match.

### The link travels in the session

It does not go through the messages framework, whose fallback storage is a
cookie. The page takes the link out of the session as it renders, so a reload
has nothing left to show. The audit trail records that we issued a link and for
whom, never the token itself.

We build the QR code in the web process with
[segno](https://pypi.org/project/segno/) and embed it as inline SVG, so the
credential never touches disk and no external image service sees it. The
copyable field stays the main route, because a QR code is reachable by neither
keyboard nor screen reader.

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
