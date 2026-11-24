"""Balance changes are atomic in SQL: a long-lived session holding a stale User can't undo other sessions' changes."""

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import AppState, Transaction, User
from app.services.ingest import ingest_path
from app.services.wallet import InsufficientFunds, credit, debit, local_user


_open: list = []


def sessions():
    """A new session that is closed after the test even if an assertion fails (else teardown hits 'locked')."""
    s = SessionLocal()
    _open.append(s)
    return s


@pytest.fixture(autouse=True)
def _close_sessions():
    yield
    while _open:
        s = _open.pop()
        s.rollback()
        s.close()


@pytest.fixture
def root(tmp_path, monkeypatch):
    d = tmp_path / "media"
    d.mkdir()
    monkeypatch.setattr(settings, "media_dir", d)
    return d


def _balance_and_ledger():
    s = sessions()
    try:
        return s.scalar(select(User.coins)), s.scalar(select(func.sum(Transaction.amount)))
    finally:
        s.close()


def _set_coins(n: int):
    """Give the local user exactly n coins, through the wallet so the ledger stays consistent."""
    s = sessions()
    u = local_user(s)
    if u.coins > n:
        debit(s, u, u.coins - n, "test_adjust")
    elif u.coins < n:
        credit(s, u, n - u.coins, "test_adjust")
    s.commit()
    s.close()


def test_scanner_credit_after_concurrent_debit_keeps_the_debit(root):
    """Mirrors race_demo.py: scanner loads 200, a request spends 200, the scanner ingests a file."""
    _set_coins(200)
    s = sessions()
    s.add(AppState(key="initial_scan_done", value="1"))
    s.commit()
    s.close()

    scan = sessions()
    su = local_user(scan)
    assert su.coins == 200

    req = sessions()
    ru = local_user(req)
    debit(req, ru, 200, "pack")
    req.commit()
    req.close()

    p = root / "new.png"
    Image.new("RGB", (40, 30), "red").save(p)
    r = ingest_path(scan, su, p, initial_scan=False)
    assert r.status == "new" and r.coins > 0
    scan.commit()
    scan.close()

    balance, ledger = _balance_and_ledger()
    assert balance == ledger  # balance always equals the ledger sum
    assert balance == r.coins  # the pack was not free


def test_balance_after_reflects_true_balance():
    _set_coins(100)
    a = sessions()
    ua = local_user(a)
    b = sessions()
    ub = local_user(b)
    credit(b, ub, 50, "x")
    b.commit()
    b.close()
    tx = credit(a, ua, 7, "y")
    assert tx.balance_after == 157 and ua.coins == 157
    a.commit()
    a.close()
    assert _balance_and_ledger() == (157, 157)


def test_debit_fails_atomically_when_balance_dropped_underneath():
    _set_coins(100)
    a = sessions()
    ua = local_user(a)
    assert ua.coins == 100  # a stale view says 100

    b = sessions()
    ub = local_user(b)
    debit(b, ub, 90, "elsewhere")
    b.commit()
    b.close()

    with pytest.raises(InsufficientFunds) as exc:
        debit(a, ua, 50, "pack")
    assert exc.value.balance == 10 and exc.value.needed == 50
    a.rollback()
    a.close()
    assert _balance_and_ledger() == (10, 10)


def test_stale_user_flush_never_writes_coins_back():
    """Changing another column on a stale User must not overwrite coins."""
    _set_coins(100)
    a = sessions()
    ua = local_user(a)
    b = sessions()
    ub = local_user(b)
    debit(b, ub, 60, "elsewhere")
    b.commit()
    b.close()
    credit(a, ua, 5, "y")
    ua.packs_opened += 1  # dirties the row; coins must not be in the UPDATE
    a.commit()
    a.close()
    assert _balance_and_ledger() == (45, 45)
