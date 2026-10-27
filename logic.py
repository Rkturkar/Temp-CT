"""Rules engine, demo-data generator, and ground-truth evaluation.

Plain Python only: no database or HTTP imports, so the rules are easy to read
and test on their own.

Rules (documented in the README):
- A temperature exactly at a limit is in range. Strictly outside is out of range.
- An out-of-range run is split wherever two readings are further apart than the
  allowed gap, so no time is bridged across missing data.
- Run duration = first out-of-range reading -> first in-range reading after it
  (or the last out-of-range reading if the run ends at a gap or the data end).
- An out-of-range run is "confirmed" only if some reading is beyond the logger's
  accuracy tolerance. Otherwise it is "uncertain".
- Status precedence: NON_COMPLIANT if a confirmed run exceeds the allowed
  duration; otherwise REVIEW_REQUIRED if there is a gap, an uncertain run, or a
  custody problem; otherwise COMPLIANT.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

COMPLIANT = "COMPLIANT"
NON_COMPLIANT = "NON_COMPLIANT"
REVIEW = "REVIEW_REQUIRED"

SAMPLE_INTERVAL = timedelta(minutes=10)
TRIP_HOURS = 12
BASE_DATE = datetime(2026, 10, 1)
DATA_DIR = Path(__file__).resolve().parent / "data"
GROUND_TRUTH_FILE = DATA_DIR / "ground_truth.json"

PRODUCTS: list[dict[str, Any]] = [
    {"code": "VAC-2-8", "name": "Vaccine (2 to 8 °C)", "min_temp_c": 2.0, "max_temp_c": 8.0,
     "max_excursion_min": 30, "max_gap_min": 30, "accuracy_c": 0.5},
    {"code": "FRZ-M15", "name": "Frozen biologic (-25 to -15 °C)", "min_temp_c": -25.0, "max_temp_c": -15.0,
     "max_excursion_min": 30, "max_gap_min": 30, "accuracy_c": 0.5},
    {"code": "CHL-0-5", "name": "Chilled food (0 to 5 °C)", "min_temp_c": 0.0, "max_temp_c": 5.0,
     "max_excursion_min": 60, "max_gap_min": 30, "accuracy_c": 0.3},
]

ORIGINS = ["Mumbai", "Pune", "Chennai", "Ahmedabad"]
DESTINATIONS = ["Delhi", "Hyderabad", "Kolkata", "Bengaluru"]

# scenario -> (weight, expected status). Used only for ground truth.
SCENARIOS: dict[str, tuple[int, str]] = {
    "normal": (40, COMPLIANT),
    "boundary": (10, COMPLIANT),
    "short_excursion": (12, COMPLIANT),
    "borderline": (8, REVIEW),
    "long_excursion": (12, NON_COMPLIANT),
    "logger_gap": (10, REVIEW),
    "custody_error": (8, REVIEW),
}


# ---------------------------------------------------------------------------
# Rules engine
# ---------------------------------------------------------------------------

def evaluate(
    product: dict[str, Any],
    readings: list[tuple[datetime, float]],
    custody: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decide compliance for one shipment from its own readings and custody events."""
    points, duplicates = _clean_readings(readings)
    max_gap = timedelta(minutes=product["max_gap_min"])
    max_excursion = product["max_excursion_min"]
    tolerance = product["accuracy_c"]

    gaps = _find_gaps(points, max_gap)
    runs = _find_excursions(points, product, max_gap)
    for run in runs:
        run["violation"] = run["confirmed"] and run["duration_min"] > max_excursion
    custody_issues = _custody_issues(custody)
    stats = _stats(points)

    if len(points) < 2:
        return {
            "status": REVIEW,
            "reasons": ["Not enough temperature readings to verify this shipment."],
            "excursions": [], "gaps": [], "custody_issues": custody_issues,
            "duplicates_removed": duplicates, "stats": stats,
        }

    violations = [r for r in runs if r["violation"]]
    uncertain = [r for r in runs if not r["confirmed"]]
    allowed = [r for r in runs if r["confirmed"] and not r["violation"]]

    reasons: list[str] = []
    for r in violations:
        reasons.append(
            f"Excursion of {r['duration_min']} min from {_fmt(r['start'])} exceeds the "
            f"{max_excursion} min limit."
        )
    for r in uncertain:
        reasons.append(
            f"Readings from {_fmt(r['start'])} are within the {tolerance} °C calibration "
            "tolerance of a limit, so the excursion cannot be confirmed."
        )
    for g in gaps:
        reasons.append(
            f"Logger gap of {g['minutes']} min from {_fmt(g['start'])} exceeds the "
            f"{product['max_gap_min']} min allowed; evidence is incomplete."
        )
    reasons.extend(custody_issues)
    for r in allowed:
        reasons.append(
            f"Excursion of {r['duration_min']} min from {_fmt(r['start'])} is within the "
            f"{max_excursion} min allowed."
        )

    if violations:
        status = NON_COMPLIANT
    elif gaps or uncertain or custody_issues:
        status = REVIEW
    else:
        status = COMPLIANT
        if not reasons:
            reasons.append("All readings are within the permitted range and the custody chain is valid.")

    return {
        "status": status,
        "reasons": reasons,
        "excursions": runs,
        "gaps": gaps,
        "custody_issues": custody_issues,
        "duplicates_removed": duplicates,
        "stats": stats,
    }


def _clean_readings(readings: list[tuple[datetime, float]]) -> tuple[list[tuple[datetime, float]], int]:
    """Sort by time and keep the first reading for any duplicated timestamp."""
    by_time: dict[datetime, float] = {}
    for ts, temp in readings:
        by_time.setdefault(ts, temp)
    return sorted(by_time.items()), len(readings) - len(by_time)


def _find_gaps(points: list[tuple[datetime, float]], max_gap: timedelta) -> list[dict[str, Any]]:
    gaps = []
    for (t1, _), (t2, _) in zip(points, points[1:]):
        if t2 - t1 > max_gap:
            gaps.append({"start": t1, "end": t2, "minutes": int((t2 - t1).total_seconds() // 60)})
    return gaps


def _find_excursions(
    points: list[tuple[datetime, float]], product: dict[str, Any], max_gap: timedelta
) -> list[dict[str, Any]]:
    lo, hi = product["min_temp_c"], product["max_temp_c"]
    runs: list[dict[str, Any]] = []
    current: list[tuple[datetime, float]] = []
    prev_ts: datetime | None = None

    for ts, temp in points:
        # A gap ends the current run at the last observed reading. Time is not bridged.
        if current and prev_ts is not None and ts - prev_ts > max_gap:
            runs.append(_make_run(current, prev_ts, lo, hi, product["accuracy_c"]))
            current = []
        if temp < lo or temp > hi:
            current.append((ts, temp))
        elif current:
            runs.append(_make_run(current, ts, lo, hi, product["accuracy_c"]))
            current = []
        prev_ts = ts

    if current:
        runs.append(_make_run(current, current[-1][0], lo, hi, product["accuracy_c"]))
    return runs


def _make_run(
    points: list[tuple[datetime, float]], end: datetime, lo: float, hi: float, tolerance: float
) -> dict[str, Any]:
    start = points[0][0]
    worst_deviation = max(max(t - hi, lo - t) for _, t in points)
    temps = [t for _, t in points]
    return {
        "start": start,
        "end": end,
        "duration_min": int((end - start).total_seconds() // 60),
        "min_temp_c": min(temps),
        "max_temp_c": max(temps),
        "confirmed": worst_deviation > tolerance,
        "violation": False,
    }


def _custody_issues(custody: list[dict[str, Any]]) -> list[str]:
    issues = []
    events = sorted(custody, key=lambda e: e["seq"])
    present = {e["event_type"] for e in events}
    for required in ("PICKUP", "DELIVERY"):
        if required not in present:
            issues.append(f"Missing {required} custody record.")
    for prev, cur in zip(events, events[1:]):
        if cur["occurred_at"] < prev["occurred_at"]:
            issues.append(
                f"Custody event {cur['event_type']} (seq {cur['seq']}) is timestamped "
                f"before seq {prev['seq']}."
            )
    return issues


def _stats(points: list[tuple[datetime, float]]) -> dict[str, Any]:
    if not points:
        return {"count": 0, "min": None, "max": None, "avg": None}
    temps = [t for _, t in points]
    return {
        "count": len(temps),
        "min": min(temps),
        "max": max(temps),
        "avg": round(sum(temps) / len(temps), 2),
    }


def _fmt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# Demo data generator (deterministic for a given seed and count)
# ---------------------------------------------------------------------------

def generate(seed: int, count: int) -> dict[str, Any]:
    """Build products, shipments, readings, custody and ground truth in memory."""
    rng = random.Random(seed)
    names = list(SCENARIOS)
    weights = [SCENARIOS[n][0] for n in names]
    shipments, truth = [], []

    for i in range(1, count + 1):
        product = rng.choice(PRODUCTS)
        scenario = rng.choices(names, weights=weights)[0]
        departed = BASE_DATE + timedelta(hours=6 * i)
        arrived = departed + timedelta(hours=TRIP_HOURS)

        temps = _baseline_temps(rng, product, TRIP_HOURS * 6 + 1)
        _apply_scenario(scenario, temps, product)
        readings = [(departed + k * SAMPLE_INTERVAL, t) for k, t in enumerate(temps)]
        if scenario == "logger_gap":
            # Drop 10 consecutive readings: the gap is 110 min, above the 30 min limit.
            mid = len(readings) // 2
            readings = readings[:mid] + readings[mid + 10:]

        code = f"SHP-2026-{i:06d}"
        shipments.append({
            "code": code,
            "product_code": product["code"],
            "origin": rng.choice(ORIGINS),
            "destination": rng.choice(DESTINATIONS),
            "departed_at": departed,
            "arrived_at": arrived,
            "readings": readings,
            "custody": _custody(departed, arrived, bad=scenario == "custody_error"),
        })
        truth.append({
            "shipment_code": code,
            "scenario": scenario,
            "expected_status": SCENARIOS[scenario][1],
        })

    return {"products": PRODUCTS, "shipments": shipments, "truth": truth}


def _baseline_temps(rng: random.Random, product: dict[str, Any], n: int) -> list[float]:
    lo, hi = product["min_temp_c"], product["max_temp_c"]
    mid, half = (lo + hi) / 2, (hi - lo) / 2
    return [round(mid + rng.uniform(-0.6, 0.6) * half, 1) for _ in range(n)]


def _apply_scenario(scenario: str, temps: list[float], product: dict[str, Any]) -> None:
    n = len(temps)
    lo, hi, acc = product["min_temp_c"], product["max_temp_c"], product["accuracy_c"]
    allowed_readings = product["max_excursion_min"] // 10  # exactly the allowed duration

    if scenario == "boundary":
        temps[n // 3] = hi
        temps[2 * n // 3] = lo
    elif scenario == "short_excursion":
        _set_run(temps, n // 3, allowed_readings, round(hi + 2.0, 1))
    elif scenario == "long_excursion":
        _set_run(temps, n // 3, allowed_readings + 4, round(hi + 2.0, 1))
    elif scenario == "borderline":
        _set_run(temps, n // 2, 4, round(hi + acc / 2, 2))


def _set_run(temps: list[float], start: int, length: int, value: float) -> None:
    for k in range(start, min(start + length, len(temps))):
        temps[k] = value


def _custody(departed: datetime, arrived: datetime, bad: bool) -> list[dict[str, Any]]:
    hub = departed - timedelta(hours=1) if bad else departed + timedelta(hours=3)
    return [
        {"seq": 1, "event_type": "PICKUP", "party": "Shipper", "location": "Origin dock", "occurred_at": departed},
        {"seq": 2, "event_type": "TRANSFER", "party": "Hub operator", "location": "Transit hub", "occurred_at": hub},
        {"seq": 3, "event_type": "DELIVERY", "party": "Carrier", "location": "Destination", "occurred_at": arrived},
    ]


# ---------------------------------------------------------------------------
# Ground truth (used only by the evaluation endpoint, never by evaluate())
# ---------------------------------------------------------------------------

def write_ground_truth(truth: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_FILE.write_text(json.dumps(truth, indent=2), encoding="utf-8")


def read_ground_truth() -> list[dict[str, Any]]:
    if not GROUND_TRUTH_FILE.exists():
        return []
    return json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def confusion(truth: list[dict[str, Any]], actual: dict[str, str]) -> dict[str, Any]:
    """Compare engine output with expected labels.

    Positive case: expected status is not COMPLIANT (a problem or insufficient evidence).
    Predicted positive: the engine returned anything other than COMPLIANT.
    """
    tp = fp = tn = fn = exact = problems = false_compliant = 0
    by_scenario: dict[str, dict[str, int]] = {}

    for row in truth:
        expected = row["expected_status"]
        got = actual.get(row["shipment_code"], REVIEW)
        expected_pos, predicted_pos = expected != COMPLIANT, got != COMPLIANT

        if expected_pos and predicted_pos:
            tp += 1
        elif expected_pos:
            fn += 1
        elif predicted_pos:
            fp += 1
        else:
            tn += 1

        if expected_pos:
            problems += 1
            if got == COMPLIANT:
                false_compliant += 1
        if got == expected:
            exact += 1

        s = by_scenario.setdefault(row["scenario"], {"total": 0, "matched": 0})
        s["total"] += 1
        s["matched"] += int(got == expected)

    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "total": len(truth),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": _ratio(2 * precision * recall, precision + recall) if precision + recall else 0.0,
        "exact_match": _ratio(exact, len(truth)),
        "problem_cases": problems,
        "false_compliant_rate": _ratio(false_compliant, problems),
        "by_scenario": by_scenario,
    }