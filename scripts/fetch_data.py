"""Fetch and score US market intelligence indicators.

Output:
  data/latest.json
  data/history.json

Environment variables:
  FRED_API_KEY              Optional. Recommended for production.
  ALPHA_VANTAGE_API_KEY     Optional. Used for ETF/stock market data.

Design note:
  V1 prioritizes robust official macro data from FRED. Market quotes are optional,
  because exchange-licensed data should ideally come from a paid/authorized provider.
"""

from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "indicators.yml"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"

FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()

USER_AGENT = "us-market-intel-dashboard/0.1 (personal research; contact: user-configured)"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_float(x: Any) -> Optional[float]:
    try:
        if x in (None, "", ".", "NaN", "nan"):
            return None
        val = float(x)
        if math.isnan(val):
            return None
        return val
    except Exception:
        return None


def get_config() -> Dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_fred_series(series_id: str, years: int = 5) -> List[Dict[str, Any]]:
    """Fetch a FRED series using API if key exists; otherwise public CSV fallback."""
    observation_start = (datetime.now(timezone.utc) - timedelta(days=365 * years)).date().isoformat()
    if FRED_API_KEY:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "observation_start": observation_start,
            "sort_order": "asc",
        }
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        rows = r.json().get("observations", [])
        out = []
        for row in rows:
            val = safe_float(row.get("value"))
            if val is not None:
                out.append({"date": row.get("date"), "value": val})
        return out

    # Public FRED graph CSV fallback. Good for personal prototype; use API key in production.
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    lines = r.text.splitlines()
    reader = csv.DictReader(lines)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=365 * years)
    out = []
    for row in reader:
        date_str = row.get("observation_date")
        value_str = row.get(series_id)
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        val = safe_float(value_str)
        if val is not None:
            out.append({"date": date_str, "value": val})
    return out


def latest_and_changes(points: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[str], Optional[float], Optional[float], Optional[float]]:
    if not points:
        return None, None, None, None, None
    points = sorted(points, key=lambda x: x["date"])
    latest = points[-1]
    v = latest["value"]
    d = latest["date"]
    prev = points[-2]["value"] if len(points) >= 2 else None
    prev5 = points[-6]["value"] if len(points) >= 6 else None
    prev20 = points[-21]["value"] if len(points) >= 21 else None
    chg1 = v - prev if prev is not None else None
    chg5 = v - prev5 if prev5 is not None else None
    chg20 = v - prev20 if prev20 is not None else None
    return v, d, chg1, chg5, chg20


def yoy_from_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not points:
        return []
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    # monthly series: compare with 12 periods back; if weekly/daily this is not used.
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["yoy"] = (df["value"] / df["value"].shift(12) - 1) * 100
    df = df.dropna(subset=["yoy"])
    return [{"date": row.date.strftime("%Y-%m-%d"), "value": float(row.yoy)} for row in df.itertuples()]


def mom_from_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not points:
        return []
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["mom"] = df["value"].diff()
    df = df.dropna(subset=["mom"])
    return [{"date": row.date.strftime("%Y-%m-%d"), "value": float(row.mom)} for row in df.itertuples()]


def fetch_alpha_vantage_daily(symbol: str) -> List[Dict[str, Any]]:
    if not ALPHA_VANTAGE_API_KEY:
        return []
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": symbol,
        "outputsize": "compact",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    payload = r.json()
    key = "Time Series (Daily)"
    if key not in payload:
        return []
    out = []
    for date_str, row in payload[key].items():
        val = safe_float(row.get("5. adjusted close") or row.get("4. close"))
        if val is not None:
            out.append({"date": date_str, "value": val})
    return sorted(out, key=lambda x: x["date"])


def risk_score(indicator_id: str, value: Optional[float], chg5: Optional[float] = None) -> int:
    """Heuristic V1 score: 0=benign, 100=high risk.
    These thresholds are intentionally transparent and should be iterated after backtests.
    """
    if value is None:
        return 50

    if indicator_id in {"dgs10"}:
        if value >= 4.70: return 85
        if value >= 4.40: return 70
        if value >= 4.10: return 55
        return 35
    if indicator_id in {"dgs2"}:
        if value >= 4.50: return 80
        if value >= 4.10: return 65
        if value >= 3.70: return 50
        return 35
    if indicator_id in {"dfii10"}:
        if value >= 2.30: return 85
        if value >= 2.00: return 70
        if value >= 1.70: return 55
        return 35
    if indicator_id in {"t10yie"}:
        if value >= 2.60: return 80
        if value >= 2.40: return 65
        if value >= 2.20: return 50
        return 35
    if indicator_id in {"cpiaucsl_yoy", "cpilfesl_yoy", "pcepi_yoy", "pcepilfe_yoy"}:
        if value >= 4.0: return 90
        if value >= 3.2: return 75
        if value >= 2.6: return 60
        if value >= 2.0: return 45
        return 35
    if indicator_id == "unrate":
        if value >= 5.0: return 80
        if value >= 4.5: return 65
        if value >= 4.0: return 45
        return 35
    if indicator_id == "icsa":
        if value >= 280_000: return 85
        if value >= 240_000: return 65
        if value >= 210_000: return 45
        return 35
    if indicator_id == "payems_mom":
        # Too weak is recession risk; too hot is rate risk. Middle is healthier.
        if value < 50: return 80
        if value < 100: return 65
        if value > 300: return 65
        return 40
    if indicator_id == "vixcls":
        if value >= 30: return 95
        if value >= 25: return 80
        if value >= 20: return 65
        if value >= 15: return 45
        return 35
    if indicator_id == "hy_oas":
        if value >= 5.0: return 95
        if value >= 4.0: return 80
        if value >= 3.2: return 60
        return 35
    if indicator_id == "ig_oas":
        if value >= 2.0: return 90
        if value >= 1.5: return 70
        if value >= 1.2: return 55
        return 35
    if indicator_id in {"wti"}:
        if value >= 100: return 80
        if value >= 85: return 65
        if value >= 70: return 45
        return 35
    if indicator_id in {"spy", "qqq", "iwm", "smh", "rsp"}:
        if chg5 is None: return 50
        pct = chg5 / max(abs(value - chg5), 1e-9) * 100
        if pct <= -5: return 80
        if pct <= -2: return 65
        if pct >= 3: return 35
        return 45
    return 50


def status_from_score(score: float) -> str:
    if score >= 70:
        return "red"
    if score >= 50:
        return "yellow"
    return "green"


def build_dashboard_payload() -> Dict[str, Any]:
    cfg = get_config()
    modules_out = []
    all_indicator_lookup: Dict[str, Dict[str, Any]] = {}

    for module_id, module in cfg["modules"].items():
        indicators_out = []
        for ind in module.get("indicators", []):
            points: List[Dict[str, Any]] = []
            source = ind.get("source")
            if source == "fred":
                points = fetch_fred_series(ind["series"])
            elif source == "fred_yoy":
                points = yoy_from_points(fetch_fred_series(ind["series"], years=8))
            elif source == "fred_mom":
                points = mom_from_points(fetch_fred_series(ind["series"], years=5))
            elif source == "market_data_provider" and ind.get("symbol"):
                points = fetch_alpha_vantage_daily(ind["symbol"])
            else:
                points = []

            value, date, chg1, chg5, chg20 = latest_and_changes(points)
            item = {
                "id": ind["id"],
                "label": ind["label"],
                "unit": ind.get("unit", ""),
                "source": source,
                "date": date,
                "value": value,
                "change_1": chg1,
                "change_5": chg5,
                "change_20": chg20,
                "risk_score": risk_score(ind["id"], value, chg5),
                "status": status_from_score(risk_score(ind["id"], value, chg5)),
            }
            indicators_out.append(item)
            all_indicator_lookup[ind["id"]] = item

        # Derived fields after base fetch.
        if module_id == "rates":
            dgs10 = all_indicator_lookup.get("dgs10", {}).get("value")
            dgs2 = all_indicator_lookup.get("dgs2", {}).get("value")
            if dgs10 is not None and dgs2 is not None:
                curve_bp = (dgs10 - dgs2) * 100
                item = {
                    "id": "curve_10y2y",
                    "label": "10Y-2Y Curve",
                    "unit": "bp",
                    "source": "derived",
                    "date": all_indicator_lookup.get("dgs10", {}).get("date"),
                    "value": curve_bp,
                    "change_1": None,
                    "change_5": None,
                    "change_20": None,
                    "risk_score": 50 if curve_bp > -50 else 65,
                    "status": "yellow" if curve_bp < -50 else "green",
                }
                indicators_out.append(item)
                all_indicator_lookup["curve_10y2y"] = item

        valid_scores = [x["risk_score"] for x in indicators_out if x.get("value") is not None or x.get("source") == "derived"]
        module_score = round(sum(valid_scores) / len(valid_scores), 1) if valid_scores else 50.0
        modules_out.append({
            "id": module_id,
            "name": module["name"],
            "weight": module["weight"],
            "score": module_score,
            "status": status_from_score(module_score),
            "indicators": indicators_out,
        })

    total_weight = sum(m["weight"] for m in modules_out)
    weighted_score = sum(m["score"] * m["weight"] for m in modules_out) / total_weight if total_weight else 50
    overall_status = status_from_score(weighted_score)

    top_risks = []
    for m in modules_out:
        for ind in m["indicators"]:
            if ind.get("risk_score", 0) >= 65 and ind.get("value") is not None:
                top_risks.append({
                    "module": m["name"],
                    "id": ind["id"],
                    "label": ind["label"],
                    "value": ind["value"],
                    "unit": ind.get("unit", ""),
                    "score": ind["risk_score"],
                })
    top_risks = sorted(top_risks, key=lambda x: x["score"], reverse=True)[:6]

    payload = {
        "generated_at": now_utc_iso(),
        "version": "v1.0",
        "overall": {
            "score": round(weighted_score, 1),
            "status": overall_status,
            "status_label": {"green": "绿色：可进攻", "yellow": "黄色：谨慎", "red": "红色：防守"}[overall_status],
            "summary": build_summary(overall_status, top_risks),
        },
        "modules": modules_out,
        "top_risks": top_risks,
        "notes": [
            "This is a rules-based market intelligence dashboard, not investment advice.",
            "ETF/stock quote fields require ALPHA_VANTAGE_API_KEY or another licensed market-data provider.",
            "FRED_API_KEY is recommended for production reliability, although a public CSV fallback is included for personal prototyping.",
        ],
    }
    return payload


def build_summary(status: str, top_risks: List[Dict[str, Any]]) -> str:
    if not top_risks:
        risk_text = "暂无突出高风险指标。"
    else:
        names = "、".join(x["label"] for x in top_risks[:3])
        risk_text = f"当前主要压力来自：{names}。"
    if status == "red":
        return f"市场处于红色防守状态。{risk_text} 优先降低追高和高波动仓位。"
    if status == "yellow":
        return f"市场处于黄色谨慎状态。{risk_text} 可以交易，但应等待确认并控制仓位。"
    return f"市场处于绿色可进攻状态。{risk_text} 风险偏好相对友好，但仍需检查数据日程。"


def append_history(payload: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({
        "generated_at": payload["generated_at"],
        "score": payload["overall"]["score"],
        "status": payload["overall"]["status"],
    })
    history = history[-365:]
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_dashboard_payload()
    LATEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    append_history(payload)
    print(f"Wrote {LATEST_PATH} with status={payload['overall']['status']} score={payload['overall']['score']}")


if __name__ == "__main__":
    main()
