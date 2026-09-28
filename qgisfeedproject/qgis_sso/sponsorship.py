# coding=utf-8
"""Offering to sponsor an account that a cascade suspended, and answering.

A suspended account comes back by getting a new sponsor. A maintainer does that
outright, in :func:`qgis_sso.revocation.reparent`. A colleague of the same
standing can only offer, and the suspended person decides: taking somebody on
is a claim about them, and being taken on is a claim about who vouches for you,
so both have to agree.

The rules about *who* may offer live in :mod:`qgis_sso.revocation` beside the
rest of the trust rules. What is here is the life of the offer itself. No view
code, so it is testable without a browser.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import revocation
from .models import OfferState, SponsorshipOffer, SsoAuditEvent


def pending_for(identity):
    """Offers waiting for this account to answer, newest first."""
    return (
        SponsorshipOffer.objects.filter(identity=identity, state=OfferState.PENDING)
        .select_related("sponsor")
        .order_by("-created_at")
    )


def pending_by(user):
    """Offers this person has made and nobody has answered yet."""
    return (
        SponsorshipOffer.objects.filter(sponsor=user, state=OfferState.PENDING)
        .select_related("identity", "identity__user")
        .order_by("-created_at")
    )


def offer(actor, identity, reason):
    """Offer to take a suspended account on, and say why.

    The reason is stored on the offer rather than asked for again at
    acceptance: it is the sponsor's sentence about why they vouch for this
    person, and it goes into the audit trail as they wrote it.
    """
    refused = revocation.sponsor_refusal(actor, identity)
    if refused:
        raise revocation.RevocationError(refused)
    reason = (reason or "").strip()
    if not reason:
        raise revocation.RevocationError(
            _("Say why you vouch for them. It goes in the audit trail.")
        )

    waiting = pending_for(identity).filter(sponsor=actor).first()
    if waiting is not None:
        # The database refuses a second open offer from the same person. Saying
        # so beats a constraint error on a page somebody was reading.
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


def accept(user, made):
    """Take the offer, which moves the sponsor and lifts the cascade.

    One transaction, so an offer never reads as accepted while the sponsor is
    still the old one. Every other offer waiting on this account is withdrawn:
    they were alternatives, and leaving them open would let a second one be
    accepted over the top of this one.
    """
    identity = made.identity
    with transaction.atomic():
        rescued = revocation.reparent(
            user, identity, made.sponsor, made.reason, offer=made
        )

        answered = timezone.now()
        made.state = OfferState.ACCEPTED
        made.responded_at = answered
        made.save(update_fields=["state", "responded_at"])

        SponsorshipOffer.objects.filter(
            identity=identity, state=OfferState.PENDING
        ).exclude(pk=made.pk).update(state=OfferState.WITHDRAWN, responded_at=answered)

        SsoAuditEvent.record(
            SsoAuditEvent.Action.SPONSORSHIP_ACCEPTED,
            user=identity.user,
            sub=identity.sub,
            sponsor=made.sponsor.username,
            reason=made.reason,
        )
    return rescued


def decline(user, made):
    """Turn an offer down. Only the person it was made to may do it."""
    if user.pk != made.identity.user_id:
        raise revocation.RevocationError(_("That offer was not made to you."))
    _answer(made, OfferState.DECLINED, SsoAuditEvent.Action.SPONSORSHIP_DECLINED)


def withdraw(actor, made):
    """Take an offer back. Only the person who made it may do it."""
    if actor.pk != made.sponsor_id:
        raise revocation.RevocationError(_("That offer is not yours to withdraw."))
    _answer(made, OfferState.WITHDRAWN, SsoAuditEvent.Action.SPONSORSHIP_WITHDRAWN)


def _answer(made, state, action):
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
