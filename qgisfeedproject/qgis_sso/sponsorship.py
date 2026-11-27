# coding=utf-8
"""Offering to sponsor an account that a cascade suspended, and answering.

A maintainer moves an account outright, in
:func:`qgis_sso.revocation.reparent`. A colleague can only offer, and the
suspended person decides. Who may offer is decided in
:mod:`qgis_sso.revocation`, and this module holds the life of an offer.
"""

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import revocation
from .models import KeycloakIdentity, OfferState, SponsorshipOffer, SsoAuditEvent


def pending_for(identity: KeycloakIdentity) -> QuerySet[SponsorshipOffer]:
    """Offers waiting for this account to answer, newest first."""
    return (
        SponsorshipOffer.objects.filter(identity=identity, state=OfferState.PENDING)
        .select_related("sponsor")
        .order_by("-created_at")
    )


def pending_by(user: User) -> QuerySet[SponsorshipOffer]:
    """Offers this person has made and nobody has answered yet."""
    return (
        SponsorshipOffer.objects.filter(sponsor=user, state=OfferState.PENDING)
        .select_related("identity", "identity__user")
        .order_by("-created_at")
    )


def offer(actor: User, identity: KeycloakIdentity, reason: str) -> SponsorshipOffer:
    """Offer to take a suspended account on, and say why.

    The reason is kept on the offer, so nobody is asked for it again.
    """
    refused = revocation.sponsor_refusal(actor, identity)
    if refused:
        raise revocation.RevocationError(refused)
    reason = (reason or "").strip()
    if not reason:
        raise revocation.RevocationError(
            _("Say why you vouch for them. It goes in the audit trail.")
        )

    if pending_for(identity).filter(sponsor=actor).exists():
        raise revocation.RevocationError(
            _("You have already offered to sponsor them. They have not answered yet.")
        )

    made = SponsorshipOffer.objects.create(
        identity=identity, sponsor=actor, reason=reason
    )
    SsoAuditEvent.record(
        SsoAuditEvent.Action.SPONSORSHIP_OFFERED,
        user=identity.user,
        sub=identity.sub,
        by=actor.username,
        reason=reason,
    )
    return made


def accept(user: User, made: SponsorshipOffer) -> list[KeycloakIdentity]:
    """Take the offer, which moves the sponsor and lifts the suspension.

    Lifting it withdraws every other offer waiting on this account. Returns who
    came back.
    """
    with transaction.atomic():
        # Locked and re-read, so a double-click cannot accept twice.
        identity = KeycloakIdentity.objects.select_for_update().get(pk=made.identity_id)
        made.refresh_from_db(fields=["state"])
        rescued = revocation.reparent(
            user, identity, made.sponsor, made.reason, offer=made
        )

        made.state = OfferState.ACCEPTED
        made.responded_at = timezone.now()
        made.save(update_fields=["state", "responded_at"])

        SsoAuditEvent.record(
            SsoAuditEvent.Action.SPONSORSHIP_ACCEPTED,
            user=identity.user,
            sub=identity.sub,
            sponsor=made.sponsor.username,
            reason=made.reason,
        )
    return rescued


def decline(user: User, made: SponsorshipOffer) -> None:
    """Turn an offer down. Only the person it was made to may do it."""
    if user.pk != made.identity.user_id:
        raise revocation.RevocationError(_("That offer was not made to you."))
    _answer(made, OfferState.DECLINED, SsoAuditEvent.Action.SPONSORSHIP_DECLINED)


def withdraw(actor: User, made: SponsorshipOffer) -> None:
    """Take an offer back. Only the person who made it may do it."""
    if actor.pk != made.sponsor_id:
        raise revocation.RevocationError(_("That offer is not yours to withdraw."))
    _answer(made, OfferState.WITHDRAWN, SsoAuditEvent.Action.SPONSORSHIP_WITHDRAWN)


def _answer(made: SponsorshipOffer, state: str, action: str) -> None:
    """Close an offer without moving anybody, and record it."""
    if not made.waiting:
        raise revocation.RevocationError(_("That offer has already been answered."))
    made.state = state
    made.responded_at = timezone.now()
    made.save(update_fields=["state", "responded_at"])
    SsoAuditEvent.record(
        action,
        user=made.identity.user,
        sub=made.identity.sub,
        sponsor=made.sponsor.username,
    )
