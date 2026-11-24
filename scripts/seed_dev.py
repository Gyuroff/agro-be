"""Сийд за разработка по docs/data-model.md §15: данните съвпадат с екраните във Figma.

`SEED_PASSWORD=… PYTHONPATH=. uv run python scripts/seed_dev.py`

Идемпотентен: мастър данните и договорите са по естествени ключове (eik, number, name, email),
операционният блок (курсове, имейли, справка, карти) се пропуска, ако вече е създаден.
Работи в `audit_context(system:seed, source=seed)`; ЕИК, телефони, номера на камиони, шофьори и цените
извън договорените в §15 са измислени — виж `HANDOFF.md`.
"""

import sys
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditSource, actor_ctx_by_code, audit_context
from app.core.clock import SOFIA
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import session_scope
from app.domain.plates import format_plate, normalize_plate
from app.models.cards import CardKind
from app.models.company import NumberSequence
from app.models.contracts import Contract, ContractPrice, ContractSide
from app.models.identity import User
from app.models.mail import EmailKind, InboundEmail, InboundStatus, MailboxPoll, PollStatus
from app.models.masterdata import (
    BillingMode,
    Contact,
    Counterparty,
    CounterpartyDomain,
    Product,
    Warehouse,
)
from app.models.reports import MatchStatus, SupplierReport, SupplierReportLine
from app.models.result import Expense, ExpenseCategory, ExpenseKind
from app.models.trips import CancelSource, ClientRequest, ClientRequestLine, RequestStatus, Trip
from app.schemas.cards import (
    CancellationPayload,
    ClientRequestLineView,
    ClientRequestPayload,
    ContractOverPayload,
    InquiryPayload,
    MailboxAlarmPayload,
    PaymentQuestionPayload,
    StockLowPayload,
    SupplierAdvanceLowPayload,
    UnknownSenderPayload,
)
from app.services.cards import CardService
from app.services.contracts import validate_contract
from app.services.report_intake import _payload as report_payload  # pyright: ignore[reportPrivateUsage]
from app.services.report_matching import match_lines, suggest_substitutes
from app.services.trips import PlannedTripData, TripService
from app.services.users import UserService

SEED_MARKER = "seed-ops-v1"  # message_id на първото писмо: има ли го, операционният блок е вече сложен
TRUCK = Decimal("25.20")
ZERO = Decimal(0)


def get_or_create[M: Base](session: Session, model: type[M], where: dict[str, Any], **values: Any) -> M:
    found = session.scalar(select(model).filter_by(**where))
    if found is not None:
        return found
    obj = model(**where, **values)
    session.add(obj)
    session.flush()
    return obj


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=SOFIA).astimezone(UTC)


# ---------------------------------------------------------------- потребители, номерации, разходи


def seed_users(session: Session) -> None:
    password = get_settings().seed_password
    if password is None:
        sys.exit("Задайте SEED_PASSWORD (парола за потребителите от сийда).")
    domain = get_settings().seed_email_domain
    partners = [
        ("georgi", "Георги Гюров", "Георги", True),
        ("partner2", "Съдружник 2", "Съдружник 2", False),
        ("partner3", "Съдружник 3", "Съдружник 3", False),
        ("partner4", "Съдружник 4", "Съдружник 4", False),
    ]
    for local, full, short, admin in partners:
        email = f"{local}@{domain}"
        if session.scalar(select(User.id).where(User.email == email)) is None:
            UserService.create(
                session,
                email=email,
                full_name=full,
                short_name=short,
                password=password.get_secret_value(),
                is_admin=admin,
            )


def seed_sequences(session: Session) -> None:
    for code, next_number in (("invoice_main", 1525), ("email_ref", 142)):
        seq = session.scalar(select(NumberSequence).where(NumberSequence.code == code))
        if seq is not None and seq.next_number < next_number:
            seq.next_number = next_number
    pilot = session.scalar(select(NumberSequence).where(NumberSequence.code == "invoice_pilot"))
    if pilot is not None:
        pilot.range_start, pilot.range_end = 9000000001, 9000009999
        pilot.next_number = max(pilot.next_number, 9000000001)
    session.flush()


def seed_expenses(session: Session) -> None:
    first = date(2026, 1, 1)
    monthly = [
        ("Заплати", "12000"),
        ("Наем", "1800"),
        ("Гориво", "1500"),
        ("Лизинг", "800"),
        ("Счетоводител", "600"),
        ("Телефони", "300"),
    ]
    for order, (name, amount) in enumerate(monthly):
        cat = get_or_create(session, ExpenseCategory, {"name": name}, sort_order=order)
        get_or_create(
            session,
            Expense,
            {"category_id": cat.id, "kind": ExpenseKind.MONTHLY.value, "start_month": first},
            amount=Decimal(amount),
        )
    other = get_or_create(session, ExpenseCategory, {"name": "Други"}, sort_order=len(monthly))
    nov = date(2026, 11, 1)
    get_or_create(
        session,
        Expense,
        {"category_id": other.id, "kind": ExpenseKind.ONE_OFF.value, "start_month": nov},
        amount=Decimal("1000"),
        end_month=nov,
        description="Ремонт на кола",
    )


# ---------------------------------------------------------------- мастър данни


def party(session: Session, name: str, eik: str, **values: Any) -> Counterparty:
    return get_or_create(session, Counterparty, {"eik": eik}, name=name, legal_name=name, **values)


def contact(session: Session, cp: Counterparty, email: str, **values: Any) -> Contact:
    found = session.scalar(select(Contact).where(Contact.email == email))
    if found is not None:
        return found
    obj = Contact(counterparty_id=cp.id, email=email, **values)
    session.add(obj)
    session.flush()
    return obj


def contract(
    session: Session,
    number: str,
    side: ContractSide,
    cp: Counterparty,
    product: Product,
    prices: list[tuple[date, str]],
    **values: Any,
) -> Contract:
    found = session.scalar(select(Contract).where(Contract.number == number))
    if found is not None:
        return found
    obj = Contract(number=number, side=side.value, counterparty_id=cp.id, product_id=product.id, **values)
    validate_contract(session, obj)
    session.add(obj)
    session.flush()
    for valid_from, price in prices:
        session.add(ContractPrice(contract_id=obj.id, valid_from=valid_from, price_per_t=Decimal(price)))
    session.flush()
    return obj


class Master:
    """Всичко от §15, до което операционният блок се обръща по име."""

    def __init__(self, session: Session) -> None:
        s = session
        self.pellets = get_or_create(s, Product, {"name": "Пелети"})
        self.npk = get_or_create(s, Product, {"name": "Тор NPK"})
        get_or_create(s, Product, {"name": "Тор"})
        self.sunflower = get_or_create(s, Product, {"name": "Слънчоглед"})

        self.agrotrade = party(s, "Агро Трейд ООД", "200000011", phone="+359 888 100 200")
        self.bioenergy = party(s, "Био Енерджи АД", "200000012")
        self.niva = party(s, "Нива Агро ООД", "200000013")
        self.ecopellet = party(s, "Еко Пелет ЕООД", "200000014")
        self.zarno = party(s, "Зърно Север ЕООД", "200000015")
        party(s, "Биофарм ЕООД", "200000016")
        fermer = party(s, "Агро Фермер ООД", "200000017")
        self.pelet = party(s, "Пелет АД", "200000021")
        self.suet = party(s, "Агро Сует ЕООД", "200000022")
        self.tor = party(s, "Тор Импекс ООД", "200000023")

        for cp, dom in (
            (self.agrotrade, "agrotrade.bg"),
            (fermer, "agro-fermer.bg"),
            (self.pelet, "peletad.bg"),
        ):
            get_or_create(s, CounterpartyDomain, {"domain": dom}, counterparty_id=cp.id)
        get_or_create(s, CounterpartyDomain, {"domain": "tor.bg"}, counterparty_id=self.tor.id)

        self.maria = contact(
            s, self.agrotrade, "logistics@agrotrade.bg", full_name="Мария Георгиева", sends_requests=True
        )
        self.pelet_orders = contact(s, self.pelet, "orders@peletad.bg", receives_orders=True)
        contact(s, self.pelet, "reports@peletad.bg", sends_reports=True)
        self.tor_orders = contact(s, self.tor, "orders@tor.bg", receives_orders=True)

        self.tervel = get_or_create(s, Warehouse, {"counterparty_id": self.pelet.id, "name": "Склад Тервел"})
        self.varna = get_or_create(s, Warehouse, {"counterparty_id": self.suet.id, "name": "Склад Варна"})
        self.tor_wh = get_or_create(s, Warehouse, {"counterparty_id": self.tor.id, "name": "Основен склад"})

        d = date
        # Покупки. Цените извън 118,00 € на П-2026-007 и количествата на П-2026-004 са измислени.
        self.p007 = contract(
            s, "П-2026-007", ContractSide.PURCHASE, self.pelet, self.pellets, [(d(2026, 1, 1), "118.00")],
            warehouse_id=self.tervel.id, order_contact_id=self.pelet_orders.id,
            contracted_qty_t=Decimal(3000), valid_from=d(2026, 1, 1),
            purchase_payment_mode="prepaid", funds_balance=Decimal("30000.00"), funds_held_by="counterparty",
            advance_buffer_type="percent", advance_buffer_value=Decimal(10),
        )  # fmt: skip
        self.p009 = contract(
            s, "П-2026-009", ContractSide.PURCHASE, self.suet, self.npk, [(d(2026, 1, 1), "380.00")],
            warehouse_id=self.varna.id, contracted_qty_t=Decimal(500), opening_drawn_qty_t=Decimal(470),
            valid_from=d(2026, 1, 1), purchase_payment_mode="pay_on_invoice",
        )  # fmt: skip
        self.p004 = contract(
            s, "П-2026-004", ContractSide.PURCHASE, self.tor, self.sunflower, [(d(2026, 1, 1), "455.00")],
            warehouse_id=self.tor_wh.id, order_contact_id=self.tor_orders.id, valid_from=d(2026, 1, 1),
            purchase_payment_mode="pay_on_invoice",
        )  # fmt: skip
        # Продажби. 2026-021: 118 преди 01.11 и 120 от 01.11; 2026-018: всеки понеделник, 15 дни.
        self.s021 = contract(
            s, "2026-021", ContractSide.SALE, self.agrotrade, self.pellets,
            [(d(2026, 10, 1), "118.00"), (d(2026, 11, 1), "120.00")],
            supply_contract_id=self.p007.id, contracted_qty_t=Decimal(1000), valid_from=d(2026, 10, 1),
        )  # fmt: skip
        self.s018 = contract(
            s, "2026-018", ContractSide.SALE, self.bioenergy, self.pellets, [(d(2026, 10, 1), "132.00")],
            supply_contract_id=self.p007.id, contracted_qty_t=Decimal(1000),
            valid_from=d(2026, 10, 1), valid_to=d(2026, 12, 31), payment_terms_days=15,
            billing_mode=BillingMode.PER_PERIOD.value, billing_rrule="FREQ=WEEKLY;BYDAY=MO",
            billing_dtstart=d(2026, 10, 5),
        )  # fmt: skip
        self.s015 = contract(
            s, "2026-015", ContractSide.SALE, self.niva, self.npk, [(d(2026, 1, 1), "410.00")],
            supply_contract_id=self.p009.id, contracted_qty_t=Decimal(2000),
            opening_drawn_qty_t=Decimal(1850), valid_from=d(2026, 1, 1),
        )  # fmt: skip
        self.s011 = contract(
            s, "2026-011", ContractSide.SALE, self.ecopellet, self.sunflower, [(d(2026, 1, 1), "480.00")],
            supply_contract_id=self.p004.id, valid_from=d(2026, 1, 1),
        )  # fmt: skip


# ---------------------------------------------------------------- операционен блок


class Ops:
    PLATE_TAIL = "ABEKMHOPCTXY"

    def __init__(self, session: Session, m: Master) -> None:
        self.s, self.m, self.trips = session, m, TripService(session)
        self._plate_no = 0
        self._mail_no = 0

    def plate(self) -> str:
        self._plate_no += 1
        n = self._plate_no
        tail = self.PLATE_TAIL[n % 12] + self.PLATE_TAIL[(n * 5) % 12]
        return f"{'CA' if n % 2 else 'CB'}{1000 + n * 7:04d}{tail}"

    def email(self, frm: str, subject: str, received: datetime, kind: EmailKind, cp: Counterparty | None,
              **values: Any) -> InboundEmail:  # fmt: skip
        self._mail_no += 1
        mail = InboundEmail(
            message_id=SEED_MARKER if self._mail_no == 1 else f"{SEED_MARKER}-{self._mail_no}",
            from_address=frm, to_addresses=["office@agro.example"], subject=subject,
            body_text=values.pop("body_text", subject), received_at=received,
            raw_path=f"seed/{self._mail_no}.eml", auth_ok=True, kind=kind.value,
            counterparty_id=cp.id if cp else None,
            **{"status": InboundStatus.NEEDS_ACTION.value, **values},
        )  # fmt: skip
        self.s.add(mail)
        self.s.flush()
        return mail

    def history_request(self, sale: Contract, cp: Counterparty, day: date) -> ClientRequest:
        mail = self.email(
            f"seed@{cp.name.split()[0].lower()}.example", f"Заявка {day:%d.%m}", at(day, 8),
            EmailKind.REQUEST, cp, status=InboundStatus.PROCESSED.value,
        )  # fmt: skip
        req = ClientRequest(
            inbound_email_id=mail.id, sale_contract_id=sale.id, period_from=day, period_to=day,
            status=RequestStatus.SENT.value,
        )  # fmt: skip
        self.s.add(req)
        self.s.flush()
        return req

    def trip(self, req: ClientRequest, sale: Contract, purchase: Contract, day: date, status: str,
             qty: Decimal = TRUCK) -> Trip:  # fmt: skip
        plate = self.plate()
        t = self.trips.create_planned(
            PlannedTripData(
                request_id=req.id, sale_contract=sale, purchase_contract=purchase, planned_date=day,
                plate=plate, planned_qty_t=qty, driver_name=f"Шофьор {self._plate_no}",
                inbound_email_id=req.inbound_email_id,
            )
        )  # fmt: skip
        if status == "completed":
            self.trips.complete(t, actual_qty_t=qty, load_date=day)
        elif status == "cancelled":
            self.trips.cancel(t, source=CancelSource.USER, reason="Отказан от клиента")
        return t

    def week(self) -> dict[str, list[Trip]]:
        m = self.m
        d = date
        by_day: dict[str, list[Trip]] = {}
        reqs = {
            "021": self.history_request(m.s021, m.agrotrade, d(2026, 11, 9)),
            "018": self.history_request(m.s018, m.bioenergy, d(2026, 11, 9)),
            "015": self.history_request(m.s015, m.niva, d(2026, 11, 9)),
            "011": self.history_request(m.s011, m.ecopellet, d(2026, 11, 9)),
        }
        sale = {
            "021": (m.s021, m.p007),
            "018": (m.s018, m.p007),
            "015": (m.s015, m.p009),
            "011": (m.s011, m.p004),
        }

        def add(key: str, day: date, status: str, tag: str) -> Trip:
            s, p = sale[key]
            t = self.trip(reqs[key], s, p, day, status)
            by_day.setdefault(tag, []).append(t)
            return t

        # Пн 9 и Вт 10 — минали
        for _ in range(3):
            add("021", d(2026, 11, 9), "completed", "mon")
        add("015", d(2026, 11, 9), "completed", "mon")
        for _ in range(3):
            add("018", d(2026, 11, 10), "completed", "tue")
        add("018", d(2026, 11, 10), "cancelled", "tue")
        add("015", d(2026, 11, 10), "completed", "tue")
        # Ср 11 — 12 планирани за справката (11 съвпадат + 1 липсва)
        for i in range(12):
            add("021" if i < 8 else "018", d(2026, 11, 11), "planned", "wed")
        # Чт 12 — „днес“: 9 курса, 5 Пелети / 3 Тор / 1 Слънчоглед; 4 натоварени, 4 планирани, 1 отказан
        # (планираните са Тор/Слънчоглед: планиран Пелет АД курс в ±1 ден от справката би излязъл „липсва“)
        for status in ("completed", "completed", "completed", "completed", "cancelled"):
            add("021", d(2026, 11, 12), status, "thu")
        for _ in range(3):
            add("015", d(2026, 11, 12), "planned", "thu")
        add("011", d(2026, 11, 12), "planned", "thu")
        # Пт 13 и Сб 14 — предстоящи
        for _ in range(4):
            add("021", d(2026, 11, 13), "planned", "fri")
        for _ in range(2):
            add("015", d(2026, 11, 14), "planned", "sat")
        return by_day

    def report(self, wed: list[Trip]) -> SupplierReport:
        """12 реда: 11 съвпадат + 1 непланиран (21,60 т); планираният камион 12 липсва."""
        m, day = self.m, date(2026, 11, 11)
        mail = self.email(
            "reports@peletad.bg", "Справка 11.11", at(day, 17, 10),
            EmailKind.REPORT, m.pelet,
        )  # fmt: skip
        rep = SupplierReport(inbound_email_id=mail.id, supplier_id=m.pelet.id)
        self.s.add(rep)
        self.s.flush()
        rows = [(t.plate_display, t.driver_name, t.planned_qty_t) for t in wed[:11]]
        rows.append((format_plate(self.plate()), "Шофьор непланиран", Decimal("21.60")))
        for no, (plate, driver, qty) in enumerate(rows, start=1):
            self.s.add(
                SupplierReportLine(
                    report_id=rep.id, row_no=no, load_date=day, plate_raw=plate,
                    plate_norm=normalize_plate(plate), driver_name=driver, qty_t=qty,
                    warehouse_id=m.tervel.id, match_status=MatchStatus.UNPLANNED.value,
                )
            )  # fmt: skip
        self.s.flush()
        rep.total_rows = len(rows)
        rep.total_qty_t = sum((r[2] for r in rows), ZERO)
        lines = match_lines(self.s, rep)
        suggest_substitutes(self.s, rep)
        CardService.create_or_get(
            self.s, CardKind.SUPPLIER_REPORT, title="Справка от Пелет АД",
            subtitle=f"{rep.total_rows} курса в справката · {rep.total_qty_t:.2f} т".replace(".", ","),
            payload=report_payload(self.s, rep, lines), dedupe_key=f"supplier_report:{mail.id}",
            counterparty_id=m.pelet.id, inbound_email_id=mail.id, supplier_report_id=rep.id,
        )  # fmt: skip
        return rep

    def request_card(self, cp: Counterparty, sale: Contract | None, to: str, days: list[date],
                     per_day: int, period_to: date, product: str, received: datetime) -> None:  # fmt: skip
        mail = self.email(f"seed@{cp.name.split()[0].lower()}.example", "Заявка за курсове", received,
                          EmailKind.REQUEST, cp)  # fmt: skip
        req = ClientRequest(
            inbound_email_id=mail.id, sale_contract_id=sale.id if sale else None,
            period_from=days[0], period_to=period_to, status=RequestStatus.DRAFT.value,
        )  # fmt: skip
        self.s.add(req)
        self.s.flush()
        views: list[ClientRequestLineView] = []
        warehouse_id = self.m.tervel.id if sale is not None and sale is self.m.s021 else None
        no = 0
        for day in days:
            for _ in range(per_day):
                no += 1
                plate = self.plate()
                self.s.add(
                    ClientRequestLine(
                        request_id=req.id, line_no=no, planned_date=day, plate_raw=plate,
                        plate_norm=normalize_plate(plate), driver_name=f"Шофьор {no}",
                        planned_qty_t=TRUCK, warehouse_id=warehouse_id,
                    )
                )  # fmt: skip
                views.append(
                    ClientRequestLineView(
                        date=day,
                        plate=format_plate(plate),
                        driver=f"Шофьор {no}",
                        qty_t=TRUCK,
                        uncertain=False,
                    )
                )
        self.s.flush()
        span = f"{days[0]:%d.%m}–{period_to:%d.%m}"
        CardService.create_or_get(
            self.s, CardKind.CLIENT_REQUEST, title=f"Нова заявка от {cp.name}",
            subtitle=f"{no} камиона · {span} · {product}",
            payload=ClientRequestPayload(
                request_id=req.id, inbound_email_id=mail.id, to=to, subject=f"Курсове {span} — {cp.name}",
                greeting=f"Здравейте,\nИзпращаме ви курсовете на {cp.name} за периода {span}:",
                lines=views, total_trucks=no, total_qty_t=TRUCK * no, has_uncertain=False,
            ),
            dedupe_key=f"client_request:{mail.id}", call_phone=cp.phone, counterparty_id=cp.id,
            inbound_email_id=mail.id, client_request_id=req.id,
        )  # fmt: skip

    def cards(self, by_day: dict[str, list[Trip]]) -> None:
        m, s, d = self.m, self.s, date
        # а) заявка на Агро Трейд — 12 камиона, 3 на ден (чт 19, пт 20, пн 23, вт 24)
        self.request_card(
            m.agrotrade, m.s021, "orders@peletad.bg",
            [d(2026, 11, 19), d(2026, 11, 20), d(2026, 11, 23), d(2026, 11, 24)], 3, d(2026, 11, 25),
            "Пелети", at(d(2026, 11, 12), 8, 42),
        )  # fmt: skip
        # в) заявка на Зърно Север — 4 камиона 19–20.11, Тор; без договор в сийда
        self.request_card(
            m.zarno, None, "", [d(2026, 11, 19), d(2026, 11, 20)], 2, d(2026, 11, 20), "Тор",
            at(d(2026, 11, 12), 9, 5),
        )  # fmt: skip
        # б) справка от Пелет АД — 11 / 1 / 1
        self.report(by_day["wed"])

        fri = by_day["fri"][:2]
        mail = self.email("orders@peletad.bg", "Отказ на курсове 13.11", at(d(2026, 11, 12), 10, 20),
                          EmailKind.CANCELLATION, m.pelet)  # fmt: skip
        CardService.create_or_get(
            s, CardKind.CANCELLATION, title="Отказани 2 курса от Пелет АД", subtitle="пт 13.11",
            payload=CancellationPayload(
                trip_ids=[t.id for t in fri], date=d(2026, 11, 13), plates=[t.plate_display for t in fri],
                supplier_name="Пелет АД", notify_supplier=False,
            ),
            dedupe_key=f"cancellation:{mail.id}", counterparty_id=m.pelet.id, inbound_email_id=mail.id,
        )  # fmt: skip
        CardService.create_or_get(
            s, CardKind.CONTRACT_OVER, title="Договорът с Нива Агро ООД не стига",
            subtitle="2026-015 · Тор NPK",
            payload=ContractOverPayload(
                contract_id=m.s015.id, contract_number="2026-015", product="Тор NPK",
                remaining_t=Decimal("74.40"), requested_t=Decimal("100.80"), shortage_t=Decimal("26.40"),
            ),
            dedupe_key=f"contract_over:{m.s015.id}", counterparty_id=m.niva.id, contract_id=m.s015.id,
        )  # fmt: skip
        CardService.create_or_get(
            s, CardKind.SUPPLIER_ADVANCE_LOW, title="Авансът при Пелет АД няма да стигне",
            subtitle="П-2026-007",
            payload=SupplierAdvanceLowPayload(
                purchase_contract_id=m.p007.id, contract_number="П-2026-007", supplier_name="Пелет АД",
                balance=Decimal("30000.00"), needed=Decimal("35683.20"), buffer=Decimal("3568.32"),
                shortfall=Decimal("9251.52"), trigger="request_send",
            ),
            dedupe_key=f"advance_low:{m.p007.id}", counterparty_id=m.pelet.id, contract_id=m.p007.id,
        )  # fmt: skip
        CardService.create_or_get(
            s, CardKind.STOCK_LOW, title="Запасът от Тор NPK по договорите привършва",
            payload=StockLowPayload(
                product="Тор NPK", contracts=1, total_t=Decimal(500), taken_t=Decimal(470),
                remaining_t=Decimal(30), remaining_pct=Decimal("6.00"),
            ),
            dedupe_key=f"stock_low:{m.npk.id}",
        )  # fmt: skip
        unk = self.email("dispatch@nov-klient.example", "Заявка за пелети", at(d(2026, 11, 12), 7, 55),
                         EmailKind.OTHER, None, from_name="Нов клиент")  # fmt: skip
        CardService.create_or_get(
            s, CardKind.UNKNOWN_SENDER, title="Имейл от непознат подател", subtitle="Заявка за пелети",
            payload=UnknownSenderPayload(
                inbound_email_id=unk.id, from_address=unk.from_address, subject="Заявка за пелети",
                suggested_counterparty_id=None, suggested_counterparty_name=None, suggestion_reason=None,
                auth_ok=True,
            ),
            dedupe_key=f"unknown_sender:{unk.id}", inbound_email_id=unk.id,
        )  # fmt: skip
        inq = self.email("kupuvach@inquiry.example", "Запитване за 50 т пелети", at(d(2026, 11, 12), 8, 10),
                         EmailKind.INQUIRY, None)  # fmt: skip
        CardService.create_or_get(
            s, CardKind.INQUIRY, title="Ново запитване", subtitle="Запитване за 50 т пелети",
            payload=InquiryPayload(
                inbound_email_id=inq.id, from_address=inq.from_address, subject="Запитване за 50 т пелети",
                product_guess="Пелети", qty_t_guess=Decimal(50), suggested_counterparty_id=None,
            ),
            dedupe_key=f"inquiry:{inq.id}", inbound_email_id=inq.id,
        )  # fmt: skip
        pay = self.email("reports@peletad.bg", "Неплатена фактура", at(d(2026, 11, 12), 9, 30),
                         EmailKind.PAYMENT_QUESTION, m.pelet)  # fmt: skip
        CardService.create_or_get(
            s, CardKind.PAYMENT_QUESTION, title="Въпрос за плащане от Пелет АД", subtitle="Неплатена фактура",
            payload=PaymentQuestionPayload(
                inbound_email_id=pay.id, counterparty_name="Пелет АД", invoice_number="2026-0412",
                subject="Неплатена фактура", received_at=pay.received_at,
            ),
            dedupe_key=f"payment_question:{pay.id}", counterparty_id=m.pelet.id, inbound_email_id=pay.id,
        )  # fmt: skip
        last_ok = at(d(2026, 11, 12), 10, 13)
        s.add(
            MailboxPoll(started_at=last_ok, finished_at=last_ok, status=PollStatus.OK.value, fetched_count=0)
        )
        CardService.create_or_get(
            s, CardKind.MAILBOX_ALARM, title="Пощата не е проверявана от 47 мин.",
            payload=MailboxAlarmPayload(last_ok_at=last_ok, minutes=47), dedupe_key="mailbox_alarm",
        )  # fmt: skip


def seed_operations(session: Session, m: Master) -> None:
    if session.scalar(select(InboundEmail.id).where(InboundEmail.message_id == SEED_MARKER)):
        return
    ops = Ops(session, m)
    ops.cards(ops.week())


def run(session: Session) -> None:
    """Целият сийд в дадена сесия (commit е на извикващия) — за `main` и за тестовете."""
    ctx = actor_ctx_by_code("system:seed", AuditSource.SEED, session=session, reason="seed_dev")
    with audit_context(ctx):
        seed_users(session)
        seed_sequences(session)
        seed_expenses(session)
        seed_operations(session, Master(session))


def main() -> None:
    with session_scope() as session:
        run(session)
    print("Сийдът е готов.")


if __name__ == "__main__":
    main()
