"""Activos y amortizaciones (asientos en Holded)."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, SettingsDep
from app.assets.amortization import (
    amortization_accounts,
    decimal,
    monthly_amount,
    post_quota,
    quipu_assets,
    schedule,
)
from app.clients import HoldedClient
from app.models import AmortizationEntry, Asset, Record
from app.schemas import (
    AssetIn,
    AssetOut,
    EntriesReport,
    EntryResult,
    ManualFlag,
    QuotaOut,
    QuotaSelection,
)

router = APIRouter(prefix="/assets", tags=["assets"])

FINANCIAL_FIELDS = ("account_code", "acquisition_date", "cost", "annual_rate", "residual_value")


def _get(session: Session, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "Activo no encontrado")
    return asset


def _quotas(asset: Asset):
    return schedule(
        asset.acquisition_date,
        decimal(asset.cost),
        decimal(asset.residual_value),
        decimal(asset.annual_rate),
    )


def _out(asset: Asset, today: date | None = None) -> AssetOut:
    today_str = (today or date.today()).isoformat()
    registered = {e.number: e for e in asset.entries}
    expense, _, accumulated = amortization_accounts(asset.account_code)
    quotas, amortized, due = [], Decimal(0), Decimal(0)
    for quota in _quotas(asset):
        entry = registered.get(quota.number)
        if entry:
            status = "manual" if entry.manual else "creada"
            amortized += quota.amount
        else:
            status = "vencida" if quota.date <= today_str else "futura"
            if status == "vencida":
                due += quota.amount
        quotas.append(
            QuotaOut(
                number=quota.number,
                date=quota.date,
                amount=str(quota.amount),
                status=status,
                holded_entry_id=entry.holded_entry_id if entry else None,
            )
        )
    amortizable = decimal(asset.cost) - decimal(asset.residual_value)
    return AssetOut(
        id=asset.id,
        name=asset.name,
        account_code=asset.account_code,
        acquisition_date=asset.acquisition_date,
        cost=asset.cost,
        annual_rate=asset.annual_rate,
        residual_value=asset.residual_value,
        quipu_ref=asset.quipu_ref,
        monthly_amount=str(
            monthly_amount(
                decimal(asset.cost), decimal(asset.residual_value), decimal(asset.annual_rate)
            )
        ),
        expense_account=expense,
        accumulated_account=accumulated,
        locked=bool(asset.entries),
        quotas=quotas,
        amortized=str(amortized),
        due=str(due),
        remaining=str(amortizable - amortized),
    )


def _apply(asset: Asset, body: AssetIn) -> None:
    asset.name = body.name.strip()
    asset.account_code = body.account_code
    asset.acquisition_date = body.acquisition_date
    asset.cost = str(body.cost)
    asset.annual_rate = str(body.annual_rate)
    asset.residual_value = str(body.residual_value)


@router.get("", response_model=list[AssetOut])
def list_assets(session: SessionDep) -> list[AssetOut]:
    return [_out(a) for a in session.scalars(select(Asset).order_by(Asset.acquisition_date))]


@router.post("", response_model=AssetOut, status_code=201)
def create_asset(body: AssetIn, session: SessionDep) -> AssetOut:
    asset = Asset()
    _apply(asset, body)
    session.add(asset)
    session.commit()
    return _out(asset)


@router.put("/{asset_id}", response_model=AssetOut)
def update_asset(asset_id: int, body: AssetIn, session: SessionDep) -> AssetOut:
    asset = _get(session, asset_id)
    if asset.entries:
        changed = AssetIn(
            name=asset.name,
            account_code=asset.account_code,
            acquisition_date=asset.acquisition_date,
            cost=decimal(asset.cost),
            annual_rate=decimal(asset.annual_rate),
            residual_value=decimal(asset.residual_value),
        )
        if any(getattr(changed, f) != getattr(body, f) for f in FINANCIAL_FIELDS):
            raise HTTPException(
                409, "Ya hay cuotas registradas en Holded: solo se puede cambiar el nombre"
            )
    _apply(asset, body)
    session.commit()
    return _out(asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: int, session: SessionDep) -> None:
    asset = _get(session, asset_id)
    if any(not e.manual for e in asset.entries):
        raise HTTPException(409, "El activo tiene asientos creados en Holded: no se puede borrar")
    session.delete(asset)
    session.commit()


@router.post("/import-quipu", response_model=list[AssetOut])
def import_from_quipu(session: SessionDep) -> list[AssetOut]:
    """Crea los bienes de inversión de los gastos extraídos de Quipu que aún no están."""
    latest = (
        select(func.max(Record.id))
        .where(Record.entity_type.in_(("expenses", "tickets")))
        .group_by(Record.entity_type, Record.source_id)
    )
    records = session.scalars(select(Record).where(Record.id.in_(latest))).all()
    existing = set(session.scalars(select(Asset.quipu_ref).where(Asset.quipu_ref.is_not(None))))
    created = []
    for found in quipu_assets((r.source_id, r.source_payload) for r in records):
        if found.ref in existing:
            continue
        asset = Asset(
            name=found.name,
            account_code=found.account_code,
            acquisition_date=found.acquisition_date,
            cost=str(found.cost),
            # Sin cuotas en Quipu de las que deducirlo: el máximo de la tabla simplificada para
            # equipos informáticos (26 %); se puede cambiar antes de crear asientos
            annual_rate=str(found.annual_rate or Decimal(26)),
            residual_value="0",
            quipu_ref=found.ref,
        )
        session.add(asset)
        created.append(asset)
    session.commit()
    return [_out(a) for a in created]


@router.post("/{asset_id}/entries", response_model=EntriesReport)
def create_entries(
    asset_id: int, body: QuotaSelection, session: SessionDep, settings: SettingsDep
) -> EntriesReport:
    """Crea en Holded los asientos de las cuotas indicadas (solo vencidas y no registradas)."""
    if not settings.holded_configured:
        raise HTTPException(400, "Falta la API key de Holded en el fichero .env")
    asset = _get(session, asset_id)
    quotas = {q.number: q for q in _quotas(asset)}
    registered = {e.number for e in asset.entries}
    today = date.today().isoformat()
    results = []
    with HoldedClient.from_settings(settings) as holded:
        for number in sorted(set(body.numbers)):
            quota = quotas.get(number)
            if quota is None:
                results.append(EntryResult(number=number, ok=False, detail="No existe esa cuota"))
            elif number in registered:
                results.append(EntryResult(number=number, ok=False, detail="Ya está en Holded"))
            elif quota.date > today:
                results.append(
                    EntryResult(number=number, ok=False, detail=f"Aún no ha vencido ({quota.date})")
                )
            else:
                try:
                    entry_id = post_quota(
                        holded, asset.name, asset.account_code, quota, len(quotas)
                    )
                except Exception as exc:
                    results.append(EntryResult(number=number, ok=False, detail=str(exc)))
                    continue
                # Se guarda en cuanto existe en Holded: así nunca se duplica
                session.add(
                    AmortizationEntry(
                        asset_id=asset.id,
                        number=number,
                        date=quota.date,
                        amount=str(quota.amount),
                        holded_entry_id=entry_id,
                    )
                )
                session.commit()
                results.append(EntryResult(number=number, ok=True, detail=entry_id))
    session.refresh(asset)
    return EntriesReport(asset=_out(asset), results=results)


@router.put("/{asset_id}/entries/{number}/manual", response_model=AssetOut)
def mark_manual(asset_id: int, number: int, body: ManualFlag, session: SessionDep) -> AssetOut:
    """Marca una cuota como ya registrada a mano en Holded (o lo deshace)."""
    asset = _get(session, asset_id)
    quota = next((q for q in _quotas(asset) if q.number == number), None)
    if quota is None:
        raise HTTPException(404, "No existe esa cuota")
    entry = next((e for e in asset.entries if e.number == number), None)
    if entry and not entry.manual:
        raise HTTPException(409, "Esa cuota la creó el importador en Holded")
    if body.manual and entry is None:
        asset.entries.append(
            AmortizationEntry(number=number, date=quota.date, amount=str(quota.amount), manual=True)
        )
    elif not body.manual and entry is not None:
        asset.entries.remove(entry)
    session.commit()
    session.refresh(asset)
    return _out(asset)
