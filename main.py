"""FastAPI backend for the small Cold Chain Monitor demo.

JSON API lives under /api. The frontend (HTML/CSS/JS) is served from /.
Run from this folder:  uvicorn main:app --reload
"""

import csv
import io
import json
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import logic
from database import Shipment, SessionLocal, engine, init_db, load_dataset, reset_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cold_chain")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DEFAULT_SHIPMENTS = 50
DEFAULT_SEED = 20261022
MAX_SHIPMENTS = 200
VALID_STATUSES = {logic.COMPLIANT, logic.NON_COMPLIANT, logic.REVIEW}
EXPORT_FIELDS = [
    "shipment_code", "product_code", "product_name", "origin", "destination", "departed_at",
    "status", "excursion_count", "excursion_minutes", "gap_count", "reasons",
]

# Tracks the most recent generation run for the Data page.
GENERATION: dict[str, Any] = {"shipments": 0, "seed": DEFAULT_SEED, "generated_at": None}


def regenerate(count: int, seed: int) -> None:
    """Replace all demo data with a freshly generated, seeded dataset."""
    data = logic.generate(seed, count)
    reset_db()
    with SessionLocal() as db:
        load_dataset(db, data)
    logic.write_ground_truth(data["truth"])
    GENERATION.update(
        shipments=count,
        seed=seed,
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0),
    )
    log.info("Generated %d shipments with seed %d", count, seed)


@asynccontextmanager
async def lifespan(_: FastAPI) -> Iterator[None]:
    init_db()
    regenerate(DEFAULT_SHIPMENTS, DEFAULT_SEED)  # demo data is reset on every start
    yield


app = FastAPI(title="Cold Chain Monitor (demo)", version="0.1.0", lifespan=lifespan)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso(dt: datetime) -> str:
    """Stored timestamps are naive UTC. Return ISO 8601 with a Z suffix."""
    return dt.replace(tzinfo=None).isoformat() + "Z"


def to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


def product_policy(p: Any) -> dict[str, Any]:
    return {
        "code": p.code, "name": p.name,
        "min_temp_c": p.min_temp_c, "max_temp_c": p.max_temp_c,
        "max_excursion_min": p.max_excursion_min, "max_gap_min": p.max_gap_min,
        "accuracy_c": p.accuracy_c,
    }


def decide(ship: Shipment) -> dict[str, Any]:
    readings = [(r.recorded_at, r.temperature_c) for r in ship.readings]
    custody = [
        {"seq": c.seq, "event_type": c.event_type, "occurred_at": c.occurred_at}
        for c in ship.custody
    ]
    return logic.evaluate(product_policy(ship.product), readings, custody)


def load_shipments(db: Session, code: str | None = None) -> list[Shipment]:
    stmt = select(Shipment).options(
        selectinload(Shipment.product),
        selectinload(Shipment.readings),
        selectinload(Shipment.custody),
    ).order_by(Shipment.code)
    if code is not None:
        stmt = stmt.where(Shipment.code == code)
    return list(db.scalars(stmt))


def confirmed_excursions(decision: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in decision["excursions"] if e["confirmed"]]


def shipment_row(ship: Shipment, decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": ship.code,
        "product_code": ship.product.code,
        "product_name": ship.product.name,
        "origin": ship.origin,
        "destination": ship.destination,
        "departed_at": iso(ship.departed_at),
        "arrived_at": iso(ship.arrived_at),
        "status": decision["status"],
        "excursion_count": len(confirmed_excursions(decision)),
    }


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": engine.url.get_backend_name()}


@app.get("/api/summary")
def summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    decided = [(s, decide(s)) for s in load_shipments(db)]
    counts = Counter(d["status"] for _, d in decided)
    excursions = [e for _, d in decided for e in confirmed_excursions(d)]

    priority = {logic.NON_COMPLIANT: 0, logic.REVIEW: 1}
    needs_action = sorted(
        ((s, d) for s, d in decided if d["status"] != logic.COMPLIANT),
        key=lambda sd: (priority[sd[1]["status"]], sd[0].code),
    )[:6]

    return {
        "total": len(decided),
        "compliant": counts[logic.COMPLIANT],
        "non_compliant": counts[logic.NON_COMPLIANT],
        "review_required": counts[logic.REVIEW],
        "open_exceptions": counts[logic.NON_COMPLIANT] + counts[logic.REVIEW],
        "excursions": len(excursions),
        "excursion_minutes": sum(e["duration_min"] for e in excursions),
        "missing_data_cases": sum(1 for _, d in decided if d["gaps"]),
        "attention": [
            {"code": s.code, "status": d["status"], "reason": d["reasons"][0] if d["reasons"] else ""}
            for s, d in needs_action
        ],
        "generation": to_jsonable(GENERATION),
    }


@app.get("/api/shipments")
def list_shipments(
    q: str | None = Query(None, max_length=64),
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")

    rows = []
    for ship in load_shipments(db):
        if q and q.lower() not in ship.code.lower():
            continue
        decision = decide(ship)
        if status and decision["status"] != status:
            continue
        rows.append(shipment_row(ship, decision))

    total = len(rows)
    pages = max(1, -(-total // size))
    start = (page - 1) * size
    return {
        "items": rows[start:start + size],
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }


@app.get("/api/shipments/{code}")
def shipment_detail(code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    found = load_shipments(db, code=code)
    if not found:
        raise HTTPException(status_code=404, detail=f"Shipment {code} not found")
    ship = found[0]
    policy = product_policy(ship.product)
    decision = decide(ship)

    return {
        "shipment": {
            **shipment_row(ship, decision),
            "origin": ship.origin,
            "destination": ship.destination,
        },
        "product": policy,
        "decision": to_jsonable(decision),
        "readings": [
            {
                "recorded_at": iso(r.recorded_at),
                "temperature_c": r.temperature_c,
                "in_range": policy["min_temp_c"] <= r.temperature_c <= policy["max_temp_c"],
            }
            for r in ship.readings
        ],
        "custody": [
            {
                "seq": c.seq, "event_type": c.event_type, "party": c.party,
                "location": c.location, "occurred_at": iso(c.occurred_at),
            }
            for c in ship.custody
        ],
    }


@app.get("/api/export/{fmt}")
def export_shipments(fmt: str, db: Session = Depends(get_db)) -> Response:
    if fmt not in ("csv", "json"):
        raise HTTPException(status_code=404, detail="Supported formats: csv, json")

    rows = []
    for ship in load_shipments(db):
        decision = decide(ship)
        excursions = confirmed_excursions(decision)
        rows.append({
            "shipment_code": ship.code,
            "product_code": ship.product.code,
            "product_name": ship.product.name,
            "origin": ship.origin,
            "destination": ship.destination,
            "departed_at": iso(ship.departed_at),
            "status": decision["status"],
            "excursion_count": len(excursions),
            "excursion_minutes": sum(e["duration_min"] for e in excursions),
            "gap_count": len(decision["gaps"]),
            "reasons": " | ".join(decision["reasons"]),
        })

    headers = {"Content-Disposition": f'attachment; filename="shipments.{fmt}"'}
    if fmt == "json":
        return Response(json.dumps(rows, indent=2), media_type="application/json", headers=headers)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers=headers)


@app.get("/api/evaluation")
def evaluation(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Compare engine output with ground truth. Ground truth is read only here."""
    truth = logic.read_ground_truth()
    if not truth:
        return {"available": False}
    actual = {s.code: decide(s)["status"] for s in load_shipments(db)}
    return {"available": True, **logic.confusion(truth, actual)}


@app.post("/api/data/generate")
def generate_data(
    count: int = Query(DEFAULT_SHIPMENTS, ge=1, le=MAX_SHIPMENTS),
    seed: int = Query(DEFAULT_SEED, ge=0),
) -> dict[str, int]:
    """Replace the demo data. Local demo only: this endpoint is not protected."""
    regenerate(count, seed)
    return {"shipments": count, "seed": seed}


# Frontend last, so /api routes take precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")