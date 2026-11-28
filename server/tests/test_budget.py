"""额度按服务端日期跨连接原子计数；不依赖第三方服务。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app.gateway.cache import BudgetUnavailable, consume_daily_budget


def test_budget_never_exceeds_limit_across_connections(tmp_path):
    path = tmp_path / "budgets.sqlite3"
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: consume_daily_budget(path, "parse", 5), range(24)))
    assert sum(results) == 5


def test_budget_is_separate_for_each_day_and_name(tmp_path):
    path = tmp_path / "budgets.sqlite3"
    first = date(2026, 11, 28)
    second = date(2026, 11, 29)
    assert consume_daily_budget(path, "parse", 1, day=first)
    assert not consume_daily_budget(path, "parse", 1, day=first)
    assert consume_daily_budget(path, "search", 1, day=first)
    assert consume_daily_budget(path, "parse", 1, day=second)


def test_zero_budget_does_not_create_state(tmp_path):
    path = tmp_path / "unused" / "budgets.sqlite3"
    assert not consume_daily_budget(path, "parse", 0)
    assert not path.exists()


def test_storage_failure_is_explicit(tmp_path):
    with pytest.raises(BudgetUnavailable):
        consume_daily_budget(tmp_path, "parse", 1)
