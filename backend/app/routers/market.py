"""The card market.

GET  /api/market                 -> the index and its history, when the market moves next, the news,
                                    the cards for sale (the 10 most valuable per treatment), what
                                    collectors want from you, and your cards whose price moved most
POST /api/market/buy {media_id, treatment} -> buy one copy of a listed card at the treatment it is
                                    listed in (402 | 409 not listed)
POST /api/market/sell-duplicates -> sell every copy beyond the first of each card and treatment
GET  /api/market/cards/{id}/history -> the card's price per tick for the chart (up to 31 days)
GET  /api/market/offer           -> the collector's offer open right now, or {offer: null}
POST /api/market/offer {accept}  -> take it or turn it down (409 when it is gone)

Selling one copy is POST /api/cards/{id}/sell (routers/economy.py); it uses the same prices.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CollectionEntry, MediaItem
from ..services import market, portfolio
from ..services.cards import card_out_many
from ..services.wallet import InsufficientFunds, local_user

router = APIRouter(prefix="/api/market", tags=["market"])


class BuyRequest(BaseModel):
    media_id: int
    treatment: str = "normal"  # v0.8.0: the treatment the card is listed in


@router.get("")
def get_market(db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    state = market.catch_up(db)
    prices = market.Prices(db, state=state)
    offers = market.listings(db, prices)
    held: dict[tuple[int, str], int] = {
        (media_id, treatment): copies
        for media_id, treatment, copies in db.execute(
            select(CollectionEntry.media_id, CollectionEntry.treatment, CollectionEntry.copies)
            .where(CollectionEntry.user_id == user.id, CollectionEntry.copies > 0))
    }
    owned_ids = sorted({m for m, _ in held} & set(prices.cards))
    movers = sorted(owned_ids, key=lambda i: -abs(prices.trend(i)))[:7]
    wanted = [i for i in state.get("wanted", []) if i in owned_ids]
    need = {o["media_id"] for rows in offers.values() for o in rows} | set(movers) | set(wanted)
    items = db.scalars(select(MediaItem).where(MediaItem.id.in_(need), MediaItem.missing.is_(False))).all() if need else []
    cards = {c["id"]: c for c in card_out_many(db, user, items)}
    return {
        **market.summary(db, state),
        "index_history": state.get("index_history", []),
        "news": market.news(db),
        # v0.8.0: per treatment (normal, foil, holo, special) the most valuable cards at that
        # treatment. A row is one copy of that treatment for sale.
        "listings": {
            t: [_listing_out(o, cards[o["media_id"]], held.get((o["media_id"], t), 0), prices.cfg)
                for o in rows if o["media_id"] in cards]
            for t, rows in offers.items()
        },
        "listings_per_treatment": prices.cfg.market.listings_per_treatment,
        "wanted": [{"media_id": i, "bonus": prices.cfg.market.wanted_bonus, "card": cards[i]} for i in wanted if i in cards],
        "movers": [{"media_id": i, "trend": prices.trend(i), "card": cards[i]} for i in movers if i in cards and prices.trend(i)],
        "offer": _offer_out(db, user, state),
        "collection_value": market.collection_value(db, user, prices),
        "collection_change": portfolio.change(db, user, prices),
        "collection_history": portfolio.dated_history(db, user, state, prices),
        # what "sell all duplicates" pays: copies × today's sell price (a sale does not move the price)
        "duplicates": market.duplicates_quote(db, user, prices),
        "sell_rate": prices.cfg.market.sell_rate,
        "buy_markup": prices.cfg.market.buy_markup,
    }


def _listing_out(row: dict, card: dict, copies: int, cfg) -> dict:
    """One listing as the page gets it. `price` is what buying costs, `value` the market price of
    that copy; `copies` how many of that treatment the player holds; `fills_slot` whether buying
    fills an empty version slot (a Foil, Holo or Special the player holds no copy of)."""
    return {
        **row,
        "number": card["number"],
        "code": card["code"],
        "title": card["title"],
        "thumb_url": card["thumb_url"],
        "copies": copies,
        "fills_slot": cfg.treatments[row["treatment"]].tier > 0 and copies == 0,
        "card": card,
    }


def _offer_out(db: Session, user, state: dict | None = None) -> dict | None:
    offer = market.current_offer(db, user, state)
    if offer is None:
        return None
    media = db.get(MediaItem, offer["media_id"])
    card = card_out_many(db, user, [media])[0] if media and not media.missing else None
    return {**offer, "card": card} if card else None


class OfferAnswer(BaseModel):
    accept: bool


@router.get("/offer")
def get_offer(db: Session = Depends(get_db)) -> dict:
    """The collector's offer that is open right now (`offer` is null when there is none)."""
    return {"offer": _offer_out(db, local_user(db))}


@router.post("/offer")
def answer_offer(body: OfferAnswer, db: Session = Depends(get_db)) -> dict:
    """Take the offer or turn it down. 409 when there is none (it expired or the copy is gone)."""
    try:
        return market.answer_offer(db, local_user(db), body.accept)
    except market.NoOffer:
        raise HTTPException(409, "That offer is gone")


@router.get("/cards/{media_id}/history")
def card_history(media_id: int, db: Session = Depends(get_db)) -> dict:
    """A card's price history for the card panel: `points` (one per tick, up to 31 days) for the
    chart and `days` (one row per day, the last 30) for the table. Prices are for a Normal copy;
    `treatment_value` gives this card's factor for the other treatments."""
    out = market.card_history(db, media_id)
    if out is None:
        raise HTTPException(404, "Card not found")
    return out


@router.post("/buy")
def buy(body: BuyRequest, db: Session = Depends(get_db)) -> dict:
    user = local_user(db)
    try:
        return market.buy(db, user, body.media_id, body.treatment)
    except market.NotListed:
        raise HTTPException(409, "That card is not for sale right now at that treatment")
    except InsufficientFunds as e:
        db.rollback()
        raise HTTPException(402, {"message": "Not enough coins", "needed": e.needed, "balance": e.balance})


@router.post("/sell-duplicates")
def sell_duplicates(db: Session = Depends(get_db)) -> dict:
    return market.sell_duplicates(db, local_user(db))
