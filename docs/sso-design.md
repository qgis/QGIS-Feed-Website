# Why single sign-on works the way it does

These are the design notes for the way this site works with `auth.qgis.org`.
Read [sso.md](sso.md) first, because it tells you what the screens do and how
to run a migration. This page covers the sharp edges. Each section says why a
rule exists, and what breaks if somebody helpfully removes it.

## Identity

### Roles are a copy, not something we own

We rebuild the Django groups and the staff and superuser flags from the token
at every sign-in. Nothing here is the source of truth, so a role added in
Keycloak grants and a role removed there revokes. The price we pay is that any
local edit is temporary. If you change the checkboxes in `/admin/` on an
account linked to the realm, it looks like it worked, and the next sign-in
undoes it. That is why the admin interface warns you beside the fields.

`SSO_MANAGED_GROUPS` limits how far that reach goes. We only add and remove the
groups named there, so a group somebody created by hand for something else
survives.

### Superuser comes from a client role, never a realm role

A realm-wide role such as `qgis-superuser`, read at sign-in, would be less work
for us. It would also make whoever holds that role for hub or plugins a
superuser here. Scoping to the `feed-qgis-org` client is the whole point. We
share the realm, and we do not share the privileges.

### `OIDC_CREATE_USER` must stay `False`

The realm can federate with LDAP. If we created an account on first token, we
would hand a feed account to everybody in the OSGeo directory. The backend also
refuses in `create_user`, so the setting and the code both have to be wrong
before that can happen.

### Client secrets live in `settings_local`, never in the environment

Anybody on the server can read the deployment's process environment, through
the Nix store and through `systemctl show`. That holds for
`OIDC_RP_CLIENT_SECRET` and `SSO_PROVISIONER_CLIENT_SECRET` alike. The web
client must never hold `manage-users`, so provisioning uses its own service
account with its own secret.

## Enrolment and invitations

### The enrolment page reads local state

We build both listings from this site's own database. Nothing waits on Keycloak
to render, so an outage there does not take the page down. We contact the realm
only when you press something.

### Invite existing user is capped, and has no paging

Each account costs us several calls to Keycloak inside one request, and there
is no task queue here, so `SSO_ADMIN_ACTION_MAX_USERS` caps what one run will
do. We left paging out on purpose. You cannot lose a selection by paging if
there is no paging.

### Linking matches on a verified address and nothing else

A username proves nothing about who somebody is, because it can belong to a
different person entirely. That is why we refused to claim one at all before
linking existed. We refuse an address that differs, and one the realm has not
verified, and we say which of the two it was, because you fix them in
different ways.

We also refuse a realm account that already belongs to somebody here. Two local
accounts on one subject would break every lookup in the app.

`proposed_roles` works out the client role from what the account can already do
here, so linking grants no privilege the person did not already hold.

### There is no self-service redemption endpoint

An earlier design handed out a signed token that created the account when
somebody redeemed it. That gives us a public page where a stranger holding a
leaked link can create a realm account with an address of their choosing. We
create the account at the moment of invitation instead, so the inviter types
the address and the attack disappears.

The same reasoning makes us refuse a second account on an address the realm
already knows. That address belongs to somebody, and enrolling onto it would
send them a setup link they never asked for.

### We check tiers twice

We check the role on offer against the inviter's tier when we render the form,
and again when they submit it, because a form is only a suggestion. Tier
governs invitations and nothing else. What somebody may do to a feed entry
comes from the mirrored Django permissions, not from where they sit in the
tree.

Quota counts the invitations still open rather than every account somebody
ever invited, so a sponsor is not punished for the people who arrived and got
to work. It is a limit on how many people may be waiting at one time, not on
how many anybody may ever invite.

Three things end an open invitation. The person signs in. The sponsor cancels
it, which is the answer for somebody who is never going to sign in. Or trust in
the account is withdrawn, leaving a place nobody could use. Without the second
of those the quota was a lifetime cap wearing another name: a mistyped address
held a place for good.

### Cancelling an invitation undoes it

A place is not a number. In this design an invitation *is* an account, created
in the shared realm at the moment of invitation, so a place stands for a real
username and a real address. Anything short of removing that account would
leave the name and the address taken, and the limit would be counting something
the inviter can reset at will.

So cancelling removes the lot: the realm account, the identity row and the local
user. The place comes back because what held it is gone, and the same person can
be invited again with the same name and address. Only the audit trail stays:
`SsoAuditEvent` keeps the username and lets its user go null, so the record that
an account existed and who ended it survives the account.

The realm goes first, and the local half follows in one transaction. If the
local half fails, the person keeps an account they cannot use and cancelling
again finishes the job, because deleting a realm user tolerates one that has
already gone. The other order would leave an enabled realm account holding the
name and the address, which is the state this exists to avoid.

Cancelling is only for an invitation this site created in full, and only before
anybody has used it. `link_method` is written as `invitation` in one place only,
the invite form, which is also the only path that creates the Django user and
the realm account together; the migration wave and invitations to people who
already have accounts here keep their own records. Somebody who has signed in
has a history, so trust is withdrawn from them instead, and
`revocation.refusal` says exactly that to anybody who tries it the other way
round.

What this bounds is how many live, unused accounts one sponsor can be holding at
a time. It does not bound how many accounts somebody creates over the years, and
nothing here does: an inviter can cancel and invite again as often as they like.
That is a deliberate limit of this model, not an oversight. What stands behind it
is the graph rather than the count, since every account names who vouched for it
and revoking a sponsor suspends their whole subtree.

## Withdrawing trust

### Revocation reaches Keycloak, suspension stays local

`mirror_roles` rebuilds Django's groups from the token at every sign-in. Taking
somebody's groups away locally achieves nothing on its own, because the next
authentication hands them straight back. So anything meant to last has to
happen in Keycloak, or be respected by the mirroring.

Revocation disables the realm account, so the block holds at `auth.qgis.org`
and does not depend on anybody asking this site. Suspension can stay local
because the mirroring already reconciles a suspended account down to no
permissions. It is meant to be the lighter of the two, because those people
have done nothing wrong.

We record somebody's roles before we remove them, so a reversal has something
to put back.

### Administrators follow the same subtree rule as everybody else

We used to skip the rule for them. That let an administrator revoke whoever
invited them, and then be suspended by their own cascade.

### An administrator cannot revoke another administrator

Removing one should need a second administrator to agree, and we have not built
that. So we refuse the case rather than allow half of it. To remove an
administrator, take the `admin` role off them in Keycloak first, then revoke.

### The revoke page names everybody it will reach

A cascade that turns out larger than you expected is the one mistake here you
cannot walk back casually, so we give you no surprises. We name every affected
person before you act, and we ask for a reason to put in the audit trail.
Opening the page changes nothing, so you can read it and leave. Seeing the page
runs the same permission check as acting on it, and an account you may not
touch looks exactly like one that does not exist, so nobody can use the page to
map the tree.

### The grace window closes, and it does not cover a new sponsor

Within `SSO_REVOCATION_GRACE_DAYS`, one action puts the roles back, re-enables
the realm account and clears the suspension across the subtree. After that the
record stays but the button goes, because reversing a revocation months later
is a re-invitation rather than an undo.

We deliberately do not gate a new sponsor the same way. Once the window closes
that route is the only way back, and gating it would leave suspended people
with nowhere to go. Before it existed they had nowhere to go: the row offered
nothing, `restore` refuses anybody who was not revoked directly, the admin is
read only, and you cannot invite an account that already exists.

A new sponsor has to sit outside the account's own subtree, because a sponsor
from inside it would close the chain into a loop. The picker lists only the
people who qualify, and the view checks what was posted against that same list.

### We keep the content, and we do not leave unpublished work live

`SSO_ON_REVOKE` says what this site does about a revoked person's work, and
`qgis_sso` itself knows nothing about entries or statuses.
`qgisfeed.trust.on_revoke` sends entries in *pending review* or *approved* back
to *draft*. Otherwise a reviewer working through the queue, with no reason to
know what just happened, could publish an entry from somebody we revoked an
hour ago. Published entries stay published, and we delete nothing and
re-attribute nothing.

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

These accounts have no password, and we refuse a setup link to anybody who has
already signed in. Removing the only passkey would lock that person out with no
way back.

## Migration

### The four admin actions stay separate

Each one does a single thing, so no step happens as a side effect of another.
Creating an account never emails anybody, and you can invite somebody again
without touching their account.

### We retire a password at the first SSO sign-in, and not before

That sign-in proves the account is reachable through Keycloak. If we took the
password from somebody who has not signed in that way yet, we would lock them
out of an account they cannot recover, so the backfill action reports those and
leaves them alone. `SSO_RETIRE_PASSWORD_ON_LOGIN = False` keeps both doors open
if a cutover needs it.

### We cannot retire local login without break-glass access

Set `LOCAL_LOGIN_ENABLED = False` with no documented, audited, shell-only
emergency path, and the next Keycloak outage locks out every administrator,
including the ones who would fix Keycloak.

---

Made with 💗 by [Kartoza](https://kartoza.com) |
[Donate!](https://github.com/sponsors/kartoza) |
[GitHub](https://github.com/qgis/QGIS-Feed-Website)
