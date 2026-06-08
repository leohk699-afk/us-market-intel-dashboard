"""Fetch and score US market intelligence indicators.

Output:
  data/latest.json
  data/history.json

Environment variables:
  FRED_API_KEY              Recommended for macro data.
  ALPHA_VANTAGE_API_KEY     Optional. Used for ETF/stock watchlist data.

V2 adds:
  - Robust per-indicator error handling: one failed series will not break the run.
  - Daily ETF/stock watchlist through Alpha Vantage.
  - Macro-to-stock risk hints for AI, high beta, and crypto-related names.
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

USER_AGENT = "us-market-intel-dashboard/0.2 (personal research; contact: user-configured)"

ALPHA_CACHE: Dict[str, List[Dict[str, Any]]] = {}

WATCHLIST_GROUPS = [
    {
        "id": "core_etfs",
        "name": "大盘核心ETF",
        "description": "判断大盘、科技成长和小盘风险偏好的基础框架。",
        "items": [
            {"symbol": "SPY", "name": "S&P 500 ETF", "category": "core"},
            {"symbol": "QQQ", "name": "Nasdaq 100 ETF", "category": "core"},
            {"symbol": "IWM", "name": "Russell 2000 ETF", "category": "small_cap"},
            {"symbol": "SMH", "name": "Semiconductor ETF", "category": "ai_semis"},
        ],
    },
    {
        "id": "ai_semis",
        "name": "AI / 半导体链",
        "description": "用于观察AI主线、算力链和半导体风险偏好是否延续。",
        "items": [
            {"symbol": "NVDA", "name": "NVIDIA", "category": "ai_semis"},
            {"symbol": "AMD", "name": "AMD", "category": "ai_semis"},
            {"symbol": "AVGO", "name": "Broadcom", "category": "ai_semis"},
        ],
    },
    {
        "id": "high_beta",
        "name": "高波动成长股",
        "description": "对利率、VIX和市场情绪变化较敏感，适合观察风险偏好强弱。",
        "items": [
            {"symbol": "TSLA", "name": "Tesla", "category": "high_beta"},
            {"symbol": "PLTR", "name": "Palantir", "category": "high_beta"},
        ],
    },
    {
        "id": "crypto_related",
        "name": "加密相关股",
        "description": "对美元流动性、纳指风险偏好和BTC波动更敏感。",
        "items": [
            {"symbol": "MSTR", "name": "MicroStrategy", "category": "crypto"},
            {"symbol": "COIN", "name": "Coinbase", "category": "crypto"},
        ],
    },
]


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


def warn(message: str) -> None:
    print(f"WARNING: {message}")


def fetch_fred_series(series_id: str, years: int = 5) -> List[Dict[str, Any]]:
    """Fetch a FRED series using API if key exists; otherwise public CSV fallback.

    V2 intentionally returns [] on per-series failures so that one unstable series
    does not break the entire dashboard update.
    """
    observation_start = (datetime.now(timezone.utc) - timedelta(days=365 * years)).date().isoformat()
    try:
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
    except Exception as exc:
        warn(f"FRED series failed: {series_id}: {exc}")
        return []


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


def pct_from_delta(value: Optional[float], delta: Optional[float]) -> Optional[float]:
    if value is None or delta is None:
        return None
    prev = value - delta
    if abs(prev) < 1e-9:
        return None
    return delta / prev * 100


def yoy_from_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not points:
        return []
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
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
    """Fetch daily close data from Alpha Vantage with in-run caching.

    Uses TIME_SERIES_DAILY because it is broadly available in free API tiers.
    If the API key is missing or rate-limited, return [] instead of failing.
    """
    symbol = symbol.upper().strip()
    if symbol in ALPHA_CACHE:
        return ALPHA_CACHE[symbol]
    if not ALPHA_VANTAGE_API_KEY:
        ALPHA_CACHE[symbol] = []
        return []

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "compact",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        payload = r.json()
        key = "Time Series (Daily)"
        if key not in payload:
            note = payload.get("Note") or payload.get("Information") or payload.get("Error Message") or str(payload)[:200]
            warn(f"Alpha Vantage returned no daily data for {symbol}: {note}")
            ALPHA_CACHE[symbol] = []
            return []
        out = []
        for date_str, row in payload[key].items():
            val = safe_float(row.get("4. close"))
            if val is not None:
                out.append({"date": date_str, "value": val})
        out = sorted(out, key=lambda x: x["date"])
        ALPHA_CACHE[symbol] = out
        return out
    except Exception as exc:
        warn(f"Alpha Vantage symbol failed: {symbol}: {exc}")
        ALPHA_CACHE[symbol] = []
        return []


def risk_score(indicator_id: str, value: Optional[float], chg5: Optional[float] = None) -> int:
    """Heuristic score: 0=benign, 100=high risk."""
    if value is None:
        return 50

    if indicator_id == "dgs10":
        if value >= 4.70: return 85
        if value >= 4.40: return 70
        if value >= 4.10: return 55
        return 35
    if indicator_id == "dgs2":
        if value >= 4.50: return 80
        if value >= 4.10: return 65
        if value >= 3.70: return 50
        return 35
    if indicator_id == "dfii10":
        if value >= 2.30: return 85
        if value >= 2.00: return 70
        if value >= 1.70: return 55
        return 35
    if indicator_id == "t10yie":
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
    if indicator_id == "wti":
        if value >= 100: return 80
        if value >= 85: return 65
        if value >= 70: return 45
        return 35
    if indicator_id in {"spy", "qqq", "iwm", "smh", "rsp"}:
        pct5 = pct_from_delta(value, chg5)
        if pct5 is None: return 50
        if pct5 <= -5: return 80
        if pct5 <= -2: return 65
        if pct5 >= 3: return 35
        return 45
    if indicator_id == "walcl":
        if chg5 is not None and chg5 < -50_000: return 65
        return 40
    if indicator_id == "rrp":
        return 45
    return 50


def status_from_score(score: float) -> str:
    if score >= 70:
        return "red"
    if score >= 50:
        return "yellow"
    return "green"


def status_label(status: str) -> str:
    return {"green": "绿色：可进攻", "yellow": "黄色：谨慎", "red": "红色：防守"}.get(status, status)


def category_label(category: str) -> str:
    labels = {
        "core": "核心ETF",
        "small_cap": "小盘风险偏好",
        "ai_semis": "AI/半导体",
        "high_beta": "高波动成长",
        "crypto": "加密相关",
    }
    return labels.get(category, category)


def stock_risk_score(category: str, pct5: Optional[float], pct20: Optional[float], macro_status: str, macro_score: float) -> int:
    if macro_status == "red":
        score = 72
    elif macro_status == "yellow":
        score = 55
    else:
        score = 38

    if category in {"high_beta", "crypto"}:
        score += 10 if macro_status == "yellow" else 18 if macro_status == "red" else 4
    elif category in {"ai_semis", "small_cap"}:
        score += 6 if macro_status in {"yellow", "red"} else 0

    if pct5 is not None:
        if pct5 <= -8:
            score += 18
        elif pct5 <= -5:
            score += 12
        elif pct5 >= 10 and category in {"high_beta", "crypto", "ai_semis"}:
            score += 8

    if pct20 is not None:
        if pct20 <= -15:
            score += 12
        elif pct20 >= 25 and category in {"high_beta", "crypto"}:
            score += 10

    return int(max(20, min(95, round(score))))


def build_stock_hint(symbol: str, category: str, status: str, pct5: Optional[float], macro_status: str) -> str:
    if status == "red":
        if category in {"crypto", "high_beta"}:
            return "防守优先；不追高，等待VIX/利率压力回落后再评估。"
        if category == "ai_semis":
            return "AI主线仍可观察，但当前不适合无确认追涨。"
        return "以观察为主，等待市场风险分回落。"

    if status == "yellow":
        if category == "crypto":
            return "仓位要轻；重点看美元、纳指和BTC风险偏好。"
        if category == "high_beta":
            return "可以观察强弱，但要等回踩或放量确认。"
        if category == "ai_semis":
            return "主线股可继续跟踪，利率上行时避免追高。"
        if category == "small_cap":
            return "小盘对利率和信用环境敏感，暂不宜激进。"
        return "维持观察，结合大盘方向决定。"

    if pct5 is not None and pct5 >= 8 and category in {"crypto", "high_beta", "ai_semis"}:
        return "风险偏好友好但短线涨幅较大，避免情绪化追高。"
    if category == "ai_semis":
        return "风险偏好较友好，可作为主线观察对象。"
    if category == "crypto":
        return "可观察，但波动大，仍需控制单票仓位。"
    return "环境相对友好，可继续跟踪趋势。"


def build_watchlist(macro_status: str, macro_score: float) -> Dict[str, Any]:
    groups_out = []
    for group in WATCHLIST_GROUPS:
        items_out = []
        for item in group["items"]:
            symbol = item["symbol"]
            points = fetch_alpha_vantage_daily(symbol)
            value, date, chg1, chg5, chg20 = latest_and_changes(points)
            pct1 = pct_from_delta(value, chg1)
            pct5 = pct_from_delta(value, chg5)
            pct20 = pct_from_delta(value, chg20)
            score = stock_risk_score(item["category"], pct5, pct20, macro_status, macro_score) if value is not None else 50
            status = status_from_score(score)
            items_out.append({
                "symbol": symbol,
                "name": item["name"],
                "category": item["category"],
                "category_label": category_label(item["category"]),
                "date": date,
                "price": value,
                "change_1_pct": pct1,
                "change_5_pct": pct5,
                "change_20_pct": pct20,
                "risk_score": score,
                "status": status,
                "status_label": status_label(status),
                "hint": build_stock_hint(symbol, item["category"], status, pct5, macro_status),
            })
        groups_out.append({
            "id": group["id"],
            "name": group["name"],
            "description": group["description"],
            "items": items_out,
        })
    return {
        "provider": "Alpha Vantage daily close",
        "note": "免费API请求数有限；V2仅做日线级观察池，不做实时交易报价。",
        "groups": groups_out,
    }


def build_dashboard_payload() -> Dict[str, Any]:
    cfg = get_config()
    modules_out = []
    all_indicator_lookup: Dict[str, Dict[str, Any]] = {}

    for module_id, module in cfg["modules"].items():
        indicators_out = []
        for ind in module.get("indicators", []):
            source = ind.get("source")
            if source == "derived":
                continue

            points: List[Dict[str, Any]] = []
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
            score = risk_score(ind["id"], value, chg5)
            item_out = {
                "id": ind["id"],
                "label": ind["label"],
                "unit": ind.get("unit", ""),
                "source": source,
                "date": date,
                "value": value,
                "change_1": chg1,
                "change_5": chg5,
                "change_20": chg20,
                "risk_score": score,
                "status": status_from_score(score),
            }
            indicators_out.append(item_out)
            all_indicator_lookup[ind["id"]] = item_out

        if module_id == "rates":
            dgs10 = all_indicator_lookup.get("dgs10", {}).get("value")
            dgs2 = all_indicator_lookup.get("dgs2", {}).get("value")
            if dgs10 is not None and dgs2 is not None:
                curve_bp = (dgs10 - dgs2) * 100
                curve_score = 50 if curve_bp > -50 else 65
                item_out = {
                    "id": "curve_10y2y",
                    "label": "10Y-2Y Curve",
                    "unit": "bp",
                    "source": "derived",
                    "date": all_indicator_lookup.get("dgs10", {}).get("date"),
                    "value": curve_bp,
                    "change_1": None,
                    "change_5": None,
                    "change_20": None,
                    "risk_score": curve_score,
                    "status": status_from_score(curve_score),
                }
                indicators_out.append(item_out)
                all_indicator_lookup["curve_10y2y"] = item_out

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

    watchlist = build_watchlist(overall_status, weighted_score)

    payload = {
        "generated_at": now_utc_iso(),
        "version": "v2.0",
        "overall": {
            "score": round(weighted_score, 1),
            "status": overall_status,
            "status_label": status_label(overall_status),
            "summary": build_summary(overall_status, top_risks),
        },
        "modules": modules_out,
        "top_risks": top_risks,
        "watchlist": watchlist,
        "notes": [
            "This is a rules-based market intelligence dashboard, not investment advice.",
            "V2 watchlist uses Alpha Vantage daily close data when ALPHA_VANTAGE_API_KEY is configured.",
            "Free market-data APIs are suitable for daily monitoring, not real-time trading execution.",
            "FRED series failures are skipped instead of breaking the whole daily update.",
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
