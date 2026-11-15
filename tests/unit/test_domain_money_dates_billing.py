from datetime import date
from decimal import Decimal

import pytest

from app.domain.billing import billing_dates, billing_label, next_billing_date, previous_billing_date
from app.domain.money import line_amount, q2, q3, vat

D = Decimal


def test_q2_q3_half_up():
    assert q2(D("32851.205")) == D("32851.21")
    assert q2(D("-0.005")) == D("-0.01")
    assert q3(D("1.0005")) == D("1.001")


def test_figma_invoice_numbers():
    amount = line_amount(D("273.760"), D("120.00"))
    assert amount == D("32851.20")
    v = vat(amount, D("20.00"))
    assert v == D("6570.24")
    assert amount + v == D("39421.44")


def test_weekly_mondays():
    got = billing_dates("FREQ=WEEKLY;BYDAY=MO", date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 20))
    assert got == [date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)]


def test_no_shift_for_weekends_and_holidays():
    # 24.12.2026 (празник) и 28.02.2027 (неделя) остават както са зададени
    thu = billing_dates("FREQ=WEEKLY;BYDAY=TH", date(2026, 12, 1), date(2026, 12, 20), date(2026, 12, 31))
    assert thu == [date(2026, 12, 24), date(2026, 12, 31)]
    last = billing_dates("FREQ=MONTHLY;BYMONTHDAY=-1", date(2027, 1, 1), date(2027, 2, 1), date(2027, 3, 5))
    assert last == [date(2027, 2, 28)]
    sat_sun = billing_dates(
        "FREQ=WEEKLY;BYDAY=SA,SU", date(2026, 11, 1), date(2026, 11, 14), date(2026, 11, 15)
    )
    assert sat_sun == [date(2026, 11, 14), date(2026, 11, 15)]


def test_next_and_previous():
    rule, dt = "FREQ=WEEKLY;BYDAY=MO", date(2026, 1, 1)
    assert next_billing_date(rule, dt, date(2026, 11, 12)) == date(2026, 11, 16)
    assert next_billing_date(rule, dt, date(2026, 11, 16)) == date(2026, 11, 16)
    assert previous_billing_date(rule, dt, date(2026, 11, 16)) == date(2026, 11, 9)
    assert previous_billing_date(rule, date(2026, 11, 10), date(2026, 11, 12)) is None
    assert next_billing_date("FREQ=DAILY;COUNT=1", date(2026, 1, 5), date(2026, 11, 12)) is None
    assert billing_dates(rule, dt, date(2026, 11, 5), date(2026, 11, 1)) == []


@pytest.mark.parametrize(
    ("mode", "rrule", "label"),
    [
        ("per_period", "FREQ=WEEKLY;BYDAY=MO", "всеки понеделник"),
        ("per_period", "FREQ=WEEKLY;BYDAY=MO,TH", "всеки понеделник и четвъртък"),
        ("per_period", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "всеки понеделник, всяка сряда и всеки петък"),
        ("per_period", "FREQ=WEEKLY;BYDAY=SU", "всяка неделя"),
        ("per_period", "FREQ=DAILY;INTERVAL=10", "на всеки 10 дни"),
        ("per_period", "FREQ=MONTHLY;BYMONTHDAY=15", "на 15-о число"),
        ("per_period", "FREQ=MONTHLY;BYMONTHDAY=-1", "последен ден от месеца"),
        ("per_trip", None, "на курс"),
    ],
)
def test_labels(mode, rrule, label):
    assert billing_label(mode, rrule) == label


@pytest.mark.parametrize("rrule", [None, "", "FREQ=YEARLY", "garbage", "FREQ=MONTHLY;BYMONTHDAY=40"])
def test_label_rejects_unsupported(rrule):
    with pytest.raises(ValueError):
        billing_label("per_period", rrule)
