import pytest

from controlled_chaos.database.connection import Database
from controlled_chaos.services.app import App
from controlled_chaos.utilities.dates import FixedClock

# 2026-10-28 10:00:00 UTC  (= 15:30 in Asia/Kolkata)
T0 = 1793181600


@pytest.fixture
def clock():
    return FixedClock(T0)


@pytest.fixture
def app(clock):
    a = App(Database(), clock)
    a.settings.set("timezone", "UTC")
    yield a
    a.close()


@pytest.fixture
def file_app(tmp_path, clock):
    a = App(Database(tmp_path / "cc.db"), clock, data_path=tmp_path)
    a.settings.set("timezone", "UTC")
    yield a
    a.close()
