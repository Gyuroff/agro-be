"""Съвпадение на справка от доставчик с планираните курсове (docs/data-model.md §9).

Детерминистично правило (бекенд, не LLM): `plate_norm` равен И `|load_date − planned_date| ≤ 1 ден` И
същият доставчик И `trip.status='planned'`. При няколко кандидата — най-близката дата, после най-малкото
`id` на курса. Статусите на курсовете не се пипат тук (само в `app/services/trips.py`).

Редовете от файла идват от парсера със `match_status='unplanned'`; `match_lines` ги поправя на `matched`
и добавя `missing` редове за планирани курсове в прозореца [мин. дата − 1, макс. дата + 1] без ред.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reports import MatchStatus, SupplierReport, SupplierReportLine
from app.models.trips import Trip, TripStatus

_TOLERANCE = timedelta(days=1)


def counts_as_matched(line: SupplierReportLine) -> bool:
    """Съвпадение, което се брои и се отбелязва при `confirm`: има тонаж.

    Ред без тонаж остава `matched` (курсът е намерен), но човекът го решава (agro-6p5.5).
    """
    return line.match_status == MatchStatus.MATCHED.value and line.qty_t is not None


def _file_lines(session: Session, report: SupplierReport) -> list[SupplierReportLine]:
    return list(
        session.scalars(
            select(SupplierReportLine)
            .where(SupplierReportLine.report_id == report.id, SupplierReportLine.row_no.is_not(None))
            .order_by(SupplierReportLine.row_no, SupplierReportLine.id)
        )
    )


def match_lines(session: Session, report: SupplierReport) -> list[SupplierReportLine]:
    """Съпоставя редовете от файла с планираните курсове и създава `missing` редове.

    Повторно извикване е безопасно: старите `missing` редове се изтриват и всичко се смята наново.
    Връща всички редове на справката (от файла + липсващите).
    """
    for old in session.scalars(
        select(SupplierReportLine).where(
            SupplierReportLine.report_id == report.id,
            SupplierReportLine.match_status == MatchStatus.MISSING.value,
        )
    ):
        for dependant in session.scalars(
            select(SupplierReportLine).where(SupplierReportLine.substitute_for_line_id == old.id)
        ):
            dependant.substitute_for_line_id = None
        session.flush()
        session.delete(old)
    session.flush()

    lines = _file_lines(session, report)
    dated = [line for line in lines if line.load_date is not None]
    if not dated:
        for line in lines:
            line.match_status, line.trip_id = MatchStatus.UNPLANNED.value, None
        session.flush()
        return lines

    low = min(line.load_date for line in dated if line.load_date) - _TOLERANCE
    high = max(line.load_date for line in dated if line.load_date) + _TOLERANCE
    trips = list(
        session.scalars(
            select(Trip)
            .where(
                Trip.supplier_id == report.supplier_id,
                Trip.status == TripStatus.PLANNED.value,
                Trip.planned_date >= low,
                Trip.planned_date <= high,
            )
            .order_by(Trip.id)
        )
    )

    # Всички допустими двойки; присвояваме първо най-близките (после най-малко trip.id, после row_no).
    pairs = sorted(
        (
            (abs((line.load_date - trip.planned_date).days), trip.id, line.row_no or 0, line, trip)
            for line in dated
            if line.plate_norm
            for trip in trips
            if trip.plate_norm == line.plate_norm
            and line.load_date is not None
            and abs(line.load_date - trip.planned_date) <= _TOLERANCE
        ),
        key=lambda p: p[:3],
    )
    matched_lines: dict[int, Trip] = {}
    taken_trips: set[int] = set()
    for _, _, _, line, trip in pairs:
        if line.id in matched_lines or trip.id in taken_trips:
            continue
        matched_lines[line.id] = trip
        taken_trips.add(trip.id)

    for line in lines:
        trip = matched_lines.get(line.id)
        line.match_status = MatchStatus.MATCHED.value if trip else MatchStatus.UNPLANNED.value
        line.trip_id = trip.id if trip else None
        line.substitute_for_line_id = None
    session.flush()

    result = list(lines)
    for trip in trips:
        if trip.id in taken_trips:
            continue
        missing = SupplierReportLine(
            report_id=report.id,
            row_no=None,
            load_date=trip.planned_date,
            plate_raw=trip.plate_display,
            plate_norm=trip.plate_norm,
            driver_name=trip.driver_name,
            qty_t=trip.planned_qty_t,
            warehouse_id=trip.warehouse_id,
            match_status=MatchStatus.MISSING.value,
            trip_id=trip.id,
        )
        session.add(missing)
        result.append(missing)
    session.flush()
    return result


def suggest_substitutes(session: Session, report: SupplierReport) -> list[SupplierReportLine]:
    """За `unplanned` ред без решение предлага `missing` ред на същия доставчик в ±1 ден.

    Задава `substitute_for_line_id` (непланираният → липсващият) и, ако складът е празен, го взема от
    курса на `missing` реда. Най-близка дата, после най-малко `id`; всеки `missing` се ползва веднъж.
    Стоката не се сравнява: редовете на справката нямат колона за стока (виж bd note agro-l2v.8).
    Връща редовете, на които е зададено предложение.
    """
    rows = list(
        session.scalars(
            select(SupplierReportLine)
            .where(SupplierReportLine.report_id == report.id)
            .order_by(SupplierReportLine.id)
        )
    )
    unplanned = [
        r
        for r in rows
        if r.match_status == MatchStatus.UNPLANNED.value and r.resolution is None and r.load_date is not None
    ]
    missing = [r for r in rows if r.match_status == MatchStatus.MISSING.value and r.load_date is not None]
    pairs = sorted(
        (
            (abs((u.load_date - m.load_date).days), m.id, u.id, u, m)
            for u in unplanned
            for m in missing
            if u.load_date is not None
            and m.load_date is not None
            and abs(u.load_date - m.load_date) <= _TOLERANCE
        ),
        key=lambda p: p[:3],
    )
    done: list[SupplierReportLine] = []
    used_u: set[int] = set()
    used_m: set[int] = set()
    for _, _, _, u, m in pairs:
        if u.id in used_u or m.id in used_m:
            continue
        used_u.add(u.id)
        used_m.add(m.id)
        u.substitute_for_line_id = m.id
        if u.warehouse_id is None:
            u.warehouse_id = m.warehouse_id
        done.append(u)
    session.flush()
    return done
