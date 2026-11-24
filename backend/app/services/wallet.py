"""Coin ledger. The ONLY place balances change: every credit/debit writes a Transaction.

Callers own the DB transaction (call db.commit() after a successful operation).

Balance changes are atomic SQL updates (`coins = coins + :d`), never read-modify-write on the
ORM object: the scanner and the watcher hold one User for a long time, and a request handler may
change the balance meanwhile. Never assign `user.coins` anywhere else.
"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..game.config import get_config
from ..models import Transaction, User


class InsufficientFunds(Exception):
    def __init__(self, needed: int, balance: int):
        super().__init__(f"needs {needed} coins, has {balance}")
        self.needed = needed
        self.balance = balance


def local_user(db: Session) -> User:
    """Single-user mode: the one local account, created with starting coins on first use.

    TODO(auth): replace with the logged-in user once accounts exist.
    """
    user = db.scalars(select(User).order_by(User.id)).first()
    if user is None:
        try:
            user = User(username="local", password_hash="", coins=0)
            db.add(user)
            db.flush()
            credit(db, user, get_config().starting_coins, "starting_grant", dedupe_key=f"starting-grant:{user.id}")
            db.commit()
        except IntegrityError:
            # Two first requests at once: the other one created the player. Use theirs.
            db.rollback()
            user = db.scalars(select(User).order_by(User.id)).one()
    return user


def top_up_starting_coins(db: Session) -> int:
    """Bring an existing player's welcome coins up to today's ``starting_coins``, once per amount.

    v0.1.1 raised the welcome coins; a player who started with less gets the difference. Never
    creates the player. Returns the coins paid (0 when nothing was owed).
    """
    user = db.scalars(select(User).order_by(User.id)).first()
    if user is None:
        return 0
    target = get_config().starting_coins
    granted = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user.id, Transaction.reason == "starting_grant"
        )
    )
    tx = credit(db, user, target - granted, "starting_grant", dedupe_key=f"starting-topup:{user.id}:{target}")
    db.commit()
    return tx.amount if tx else 0


def already_paid(db: Session, dedupe_key: str) -> bool:
    return db.scalar(select(Transaction.id).where(Transaction.dedupe_key == dedupe_key)) is not None


def _apply(db: Session, user: User, delta: int, *, require: int = 0) -> int | None:
    """Atomically add delta to the stored balance and return the new balance.

    With require > 0 the change only applies while the stored balance is at least `require`
    (returns None otherwise). The in-memory user.coins is refreshed without marking it dirty,
    so a later flush of this User never writes a stale balance back.
    """
    stmt = update(User).where(User.id == user.id)
    if require:
        stmt = stmt.where(User.coins >= require)
    stmt = stmt.values(coins=User.coins + delta).returning(User.coins)
    new = db.execute(stmt, execution_options={"synchronize_session": False}).scalar_one_or_none()
    if new is None:
        return None
    set_committed_value(user, "coins", new)
    return new


def credit(
    db: Session, user: User, amount: int, reason: str, *, dedupe_key: str | None = None, ref: str = ""
) -> Transaction | None:
    """Add coins. Returns None (and pays nothing) for zero amounts or an already-used dedupe_key."""
    if amount <= 0 or (dedupe_key and already_paid(db, dedupe_key)):
        return None
    new = _apply(db, user, amount)
    tx = Transaction(user_id=user.id, amount=amount, reason=reason, ref=ref, dedupe_key=dedupe_key, balance_after=new)
    db.add(tx)
    db.flush()
    return tx


def mark_claimed(db: Session, user: User, reason: str, *, dedupe_key: str, ref: str = "") -> Transaction | None:
    """Record a one-time reward that pays no coins (v0.9.0: rewards are sealed packs): a ledger row
    with amount 0 and the reward's dedupe key, so `already_paid` answers for old coin rewards and
    new pack rewards alike. Returns None when the key was used before (nothing may be given)."""
    if already_paid(db, dedupe_key):
        return None
    balance = db.scalar(select(User.coins).where(User.id == user.id)) or 0
    tx = Transaction(user_id=user.id, amount=0, reason=reason, ref=ref, dedupe_key=dedupe_key, balance_after=balance)
    db.add(tx)
    db.flush()  # the unique dedupe_key index rejects a concurrent duplicate here (IntegrityError)
    return tx


def take_back(db: Session, user: User, amount: int, reason: str, *, ref: str = "") -> int:
    """Take coins back, as many of `amount` as the balance holds (never below zero). Returns what
    was taken; a ledger row is written when that is more than nothing."""
    if amount <= 0:
        return 0
    for _ in range(5):  # the balance may change between the read and the update
        balance = db.scalar(select(User.coins).where(User.id == user.id)) or 0
        take = min(amount, max(0, balance))
        if take <= 0:
            set_committed_value(user, "coins", balance)
            return 0
        new = _apply(db, user, -take, require=take)
        if new is not None:
            db.add(Transaction(user_id=user.id, amount=-take, reason=reason, ref=ref, balance_after=new))
            db.flush()
            return take
    return 0


def debit(db: Session, user: User, amount: int, reason: str, *, ref: str = "") -> Transaction:
    """Spend coins, atomically: fails with InsufficientFunds if the stored balance is too low."""
    if amount < 0:
        raise ValueError("debit amount must be positive")
    new = _apply(db, user, -amount, require=amount)
    if new is None:
        balance = db.scalar(select(User.coins).where(User.id == user.id)) or 0
        set_committed_value(user, "coins", balance)
        raise InsufficientFunds(amount, balance)
    tx = Transaction(user_id=user.id, amount=-amount, reason=reason, ref=ref, balance_after=new)
    db.add(tx)
    db.flush()
    return tx
