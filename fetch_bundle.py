#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
psy64-relay — 心理64 GitHub Actions 中繼（fetch_bundle.py）

定稿：consult-20261127-psych64/verdict.md §6（64 行凍結定義）、§8（證偽門檻）
契約：apps-script/factor-fleet/Psy64.gs 開頭註解（中繼 JSON 契約）
移植來源：src/index.js（Cloudflare Worker 版）逐行對照移植，來源清單、
逾時、缺值處理、契約欄位皆與 Worker 版一致；Worker 版留作備案。

主人方法論（2026-11-27 11:24 立規，全程約束）：
 1. 64 條全數上線（定稿 §9.3「只做試點 10 個」已被主人 11:26 推翻）。
 2. 沒數據產生前，任何一條線都不可先標成噪音、刪掉或降權；刪除候選
    3 行（PSY39、PSY40、PSY48）照樣出現在 bundle 裡，缺值寫 null。
 3. 不准預設立場：本程式只出原始代理值 x，絕不在這裡算情緒分數、
    不寫死符號、權重或因果。分數由 Psy64.gs 按定稿 §6.0 統一式算；
    領先／落後、黃金是否反轉，一律由數據事後判定。
 4. 心理學是統計學、市場是考古學；策略邏輯只寫順勢，不寫逆勢抄底。

資料來源皆為公開端點、無任何 key（目前程式內沒有任何 API key；
Glassnode／Coinglass／LunarCrush 等付費牆來源一律出 null，不硬撐）。

運作（由 .github/workflows/hourly.yml 每小時第 17 分觸發）：
 - 平行拉取 OKX / Deribit / CoinGecko / Alternative.me / FRED / GDELT /
   edgeornot（單一來源逾時 10 秒，失敗不拖垮整包）
 - 打包 192 筆（PSY01–PSY64 × BTC／ETH／XAU）→ bundle.json（repo 根目錄，
   最新一包）＋ history/<台北小時>.json（append-only，保留近 45 天）
   ＋ state.json（上一小時原始值，供變化率／last_valid_time 用）
 - 試算表 Psy64.gs 每小時 GET raw.githubusercontent.com 上的 bundle.json 一次
 - 缺值一律寫 null 並填 missing_reason，絕不以 0 代替（0 是中性觀測，不是缺失）
 - Binance 全線 451 地理封鎖：本程式不依賴 Binance；
   PSY06 多空比、PSY36 主動賣出比先出 null，待 OKX rubik 端點驗證或非封鎖出口。
 - 標準函式庫為主（urllib／json／datetime／concurrent.futures），無第三方依賴。
"""

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    TAIPEI = ZoneInfo("Asia/Taipei")
except Exception:  # 系統無 tzdata 時退回固定 +08:00（台北無夏令時間，安全）
    TAIPEI = timezone(timedelta(hours=8))

BASE = Path(__file__).resolve().parent
TIMEOUT = 10            # 單一來源逾時（秒）
HISTORY_KEEP_DAYS = 45  # 歷史包保留天數
ASSETS = ["BTC", "ETH", "XAU"]
USER_AGENT = "psy64-relay/1.0 (+psych64)"


# ---------- 時間（台北） ----------
def taipei_now():
    return datetime.now(TAIPEI)


def taipei_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def taipei_iso_from_ms(ms):
    if ms is None:
        return None
    return taipei_iso(datetime.fromtimestamp(ms / 1000.0, TAIPEI))


def hour_floor(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def hour_key(dt):
    return dt.strftime("%Y-%m-%dT%H")


# ---------- 通用抓取 ----------
def fetch_json(url, timeout=TIMEOUT):
    """回傳 {ok, status, data, text, ms, error}，失敗不拋錯。"""
    t0 = time.monotonic()
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except Exception:
                data = None
            return {"ok": 200 <= resp.status < 300, "status": resp.status,
                    "data": data, "text": text, "ms": int((time.monotonic() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", errors="replace")
        except Exception:
            text = ""
        try:
            data = json.loads(text) if text else None
        except Exception:
            data = None
        return {"ok": False, "status": e.code, "data": data, "text": text,
                "ms": int((time.monotonic() - t0) * 1000), "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "status": 0, "data": None, "text": "",
                "ms": int((time.monotonic() - t0) * 1000), "error": str(e)}


def fetch_text(url, timeout=TIMEOUT):
    return fetch_json(url, timeout=timeout)


def sleep(sec):
    time.sleep(sec)


# ---------- 數值小工具（只算原始代理值，不算情緒分數） ----------
def num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def log_returns(prices):
    out = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0 and prices[i] > 0:
            out.append(math.log(prices[i] / prices[i - 1]))
    return out


def mean(a):
    return sum(a) / len(a) if a else None


def stdev(a):
    if len(a) < 2:
        return None
    m = mean(a)
    return math.sqrt(sum((v - m) ** 2 for v in a) / (len(a) - 1))


def median(a):
    if not a:
        return None
    s = sorted(a)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def autocorr1(rets):
    if len(rets) < 3:
        return None
    m = mean(rets)
    numerator = sum((rets[i] - m) * (rets[i - 1] - m) for i in range(1, len(rets)))
    den = sum((v - m) ** 2 for v in rets)
    return numerator / den if den != 0 else None


def up_streak(prices):
    n = 0
    for i in range(len(prices) - 1, 0, -1):
        if prices[i] > prices[i - 1]:
            n += 1
        else:
            break
    return n


# ---------- 缺值原因（統一字串，寫進 missing_reason） ----------
MR = {
    "NO_HOURLY": "無每小時公開來源",
    "PAYWALL": "須 API key／付費牆，免費層無每小時數據",
    "GEO_BINANCE": "Binance 地理封鎖（451），本程式不依賴 Binance",
    "WARMUP": "暖機：無上一小時原始值，無法算變化率",
    "FETCH_FAIL": "來源抓取失敗（HTTP 非 200／逾時／解析失敗）",
    "EVENT_ONLY": "事件驅動，無自動事件源",
    "NLP_MISSING": "需 NLP／社群掃描，未部署",
    "NO_ENDPOINT": "無公開端點可取該代理值",
    "UNVERIFIED": "端點未驗證（step0 未列出），首次嘗試失敗",
}

# ---------- 來源抓取（每函式回傳 dict，失敗不拋錯） ----------
OKX = "https://www.okx.com"


def fetch_okx(path):
    return fetch_json(f"{OKX}{path}")


def get_funding():
    """OKX 資金費率（BTC/ETH 永續，每 8h 結算，每小時可取最新值；step0 已驗 200）"""
    out = {}
    for sym in ("BTC", "ETH"):
        r = fetch_okx(f"/api/v5/public/funding-rate?instId={sym}-USDT-SWAP")
        row = None
        if r["ok"] and isinstance(r["data"], dict) and isinstance(r["data"].get("data"), list) and r["data"]["data"]:
            row = r["data"]["data"][0]
        out[sym] = {"rate": num(row.get("fundingRate")), "time": num(row.get("fundingTime"))} if row else None
        out[sym + "_meta"] = {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}
    return out


def get_open_interest():
    """OKX 未平倉（step0 已驗 200）"""
    out = {}
    for sym in ("BTC", "ETH"):
        r = fetch_okx(f"/api/v5/public/open-interest?instId={sym}-USDT-SWAP")
        row = None
        if r["ok"] and isinstance(r["data"], dict) and isinstance(r["data"].get("data"), list) and r["data"]["data"]:
            row = r["data"]["data"][0]
        if row:
            oi = num(row.get("oiUsd")) if row.get("oiUsd") is not None else num(row.get("oi"))
            out[sym] = {"oi": oi, "time": num(row.get("ts"))}
        else:
            out[sym] = None
        out[sym + "_meta"] = {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}
    return out


def get_candles(inst_id, limit=100):
    """OKX K 線（1H，近 100 根；回傳時間遞增陣列）。
    注意：step0 實測 history-candles 僅回至約 4–5 天前；180 天基準須靠 history/ 自建。"""
    r = fetch_okx(f"/api/v5/market/candles?instId={inst_id}&bar=1H&limit={limit}")
    if (not r["ok"] or not isinstance(r["data"], dict) or r["data"].get("code") != "0"
            or not isinstance(r["data"].get("data"), list)):
        return {"ok": False, "candles": [], "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    candles = []
    for c in r["data"]["data"]:
        row = {"t": num(c[0]), "o": num(c[1]), "h": num(c[2]), "l": num(c[3]), "c": num(c[4]), "v": num(c[5])}
        if row["t"] and row["c"]:
            candles.append(row)
    candles.sort(key=lambda x: x["t"])
    return {"ok": True, "candles": candles, "meta": {"status": r["status"], "ms": r["ms"]}}


def get_rubik(kind, inst_id):
    """OKX rubik 多空比／主動成交量（step0 未列出，未驗證；失敗即 null，不硬撐）"""
    if kind == "lsr":
        path = f"/api/v5/rubik/stat/contracts/long-short-account-ratio?instId={inst_id}&period=1H&limit=1"
    else:
        path = f"/api/v5/rubik/stat/taker-volume?instId={inst_id}&period=1H&limit=1"
    r = fetch_okx(path)
    if (not r["ok"] or not isinstance(r["data"], dict) or r["data"].get("code") != "0"
            or not isinstance(r["data"].get("data"), list) or not r["data"]["data"]):
        return {"ok": False, "row": None, "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    return {"ok": True, "row": r["data"]["data"][0], "meta": {"status": r["status"], "ms": r["ms"]}}


def get_dvol():
    """Deribit 波動率指數 DVOL（step0：Deribit 可達 200；25Δ 偏態見 get_skew）"""
    out = {}
    for cur in ("BTC", "ETH"):
        r = fetch_json(f"https://www.deribit.com/api/v2/public/get_volatility_index?currency={cur}")
        v = None
        if r["ok"] and isinstance(r["data"], dict) and isinstance(r["data"].get("result"), dict):
            v = num(r["data"]["result"].get("dvol") or r["data"]["result"].get("volatility_index"))
        out[cur] = {"dvol": v} if v is not None else None
        out[cur + "_meta"] = {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}
    return out


def _best_delta_iv(instruments):
    """在候選期權中取 |delta| 最接近 0.25 者的 mark IV；取不到回傳 None。"""
    best = None
    best_score = None

    def probe(o):
        r = fetch_json(
            "https://www.deribit.com/api/v2/public/ticker?instrument_name="
            + urllib.request.quote(o["instrument_name"]),
            timeout=5,
        )
        if not r["ok"] or not isinstance(r["data"], dict) or not isinstance(r["data"].get("result"), dict):
            return None
        res = r["data"]["result"]
        iv = num(res.get("mark_iv"))
        d = num((res.get("greeks") or {}).get("delta"))
        if iv is None or d is None:
            return None
        return iv, abs(abs(d) - 0.25)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(probe, o): o for o in instruments}
        for fut in as_completed(futs):
            got = fut.result()
            if got is None:
                continue
            iv, score = got
            if best_score is None or score < best_score:
                best, best_score = iv, score
    return best


def get_skew(currency, spot):
    """Deribit 25Δ put−call IV 偏態（最佳努力，bounded：最近到期、現價 ±15% 內
    最多 3 call＋3 put）。失敗即 null，不編數字。部署後須驗證。"""
    try:
        inst = fetch_json(
            f"https://www.deribit.com/api/v2/public/get_instruments?currency={currency}&kind=option&expired=false"
        )
        if not inst["ok"] or not isinstance(inst["data"], dict) or not isinstance(inst["data"].get("result"), list):
            return {"ok": False, "skew": None, "meta": {"status": inst["status"]}}
        opts = [o for o in inst["data"]["result"]
                if o.get("expiration_timestamp") and o.get("strike_price")]
        if not opts:
            return {"ok": False, "skew": None, "meta": {"status": 200, "error": "no instruments"}}
        nearest_exp = min(o["expiration_timestamp"] for o in opts)
        near = [o for o in opts
                if o["expiration_timestamp"] == nearest_exp
                and abs(o["strike_price"] - spot) / spot <= 0.15]
        calls = [o for o in near if o.get("option_type") == "call"][:3]
        puts = [o for o in near if o.get("option_type") == "put"][:3]
        if not calls or not puts:
            return {"ok": False, "skew": None, "meta": {"status": 200, "error": "no near strikes"}}
        call_iv = _best_delta_iv(calls)
        put_iv = _best_delta_iv(puts)
        if call_iv is None or put_iv is None:
            return {"ok": False, "skew": None, "meta": {"status": 200, "error": "delta/iv missing"}}
        return {"ok": True, "skew": put_iv - call_iv, "meta": {"status": 200}}
    except Exception as e:
        return {"ok": False, "skew": None, "meta": {"status": 0, "error": str(e)}}


# CoinGecko（免費層有速率限制：循序呼叫、間隔 300ms；429 即 null）
CG = "https://api.coingecko.com/api/v3"


def get_cg_simple():
    r = fetch_json(f"{CG}/simple/price?ids=bitcoin,ethereum,tether-gold&vs_currencies=usd")
    if not r["ok"] or not isinstance(r["data"], dict):
        return {"ok": False, "prices": {}, "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    d = r["data"]
    return {"ok": True,
            "prices": {"BTC": num((d.get("bitcoin") or {}).get("usd")),
                       "ETH": num((d.get("ethereum") or {}).get("usd")),
                       "XAU": num((d.get("tether-gold") or {}).get("usd"))},
            "meta": {"status": r["status"], "ms": r["ms"]}}


def get_cg_chart(coin_id, days, interval):
    r = fetch_json(f"{CG}/coins/{coin_id}/market_chart?vs_currency=usd&days={days}&interval={interval}")
    if not r["ok"] or not isinstance(r["data"], dict) or not isinstance(r["data"].get("prices"), list):
        return {"ok": False, "prices": [], "volumes": [],
                "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    prices = [{"t": t, "p": num(p)} for t, p in r["data"]["prices"]]
    prices = [x for x in prices if x["p"] is not None]
    volumes = []
    if isinstance(r["data"].get("total_volumes"), list):
        volumes = [{"t": t, "v": num(v)} for t, v in r["data"]["total_volumes"]]
        volumes = [x for x in volumes if x["v"] is not None]
    return {"ok": True, "prices": prices, "volumes": volumes,
            "meta": {"status": r["status"], "ms": r["ms"]}}


def get_cg_stable():
    r = fetch_json(f"{CG}/coins/markets?vs_currency=usd&ids=tether,usd-coin&price_change_percentage=24h")
    if not r["ok"] or not isinstance(r["data"], list):
        return {"ok": False, "mcap": None, "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    mcap = sum((num(c.get("market_cap")) or 0) for c in r["data"])
    return {"ok": True, "mcap": mcap or None, "meta": {"status": r["status"], "ms": r["ms"]}}


def get_fgi():
    """Alternative.me 恐懼貪婪指數（日更；step0 已驗 200，FGI=12）"""
    r = fetch_json("https://api.alternative.me/fng/?limit=1")
    row = None
    if r["ok"] and isinstance(r["data"], dict) and isinstance(r["data"].get("data"), list) and r["data"]["data"]:
        row = r["data"]["data"][0]
    if not row:
        return {"ok": False, "value": None, "time": None,
                "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    ts = num(row.get("timestamp"))
    return {"ok": True, "value": num(row.get("value")),
            "time": ts * 1000 if ts is not None else None,
            "meta": {"status": r["status"], "ms": r["ms"]}}


def get_fred():
    """FRED 10 年 TIPS 殖利率 DFII10（日更；step0 從 VM 逾時，Actions 出口待驗證）"""
    r = fetch_text("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10")
    if not r["ok"] or not r["text"]:
        return {"ok": False, "value": None, "time": None, "history": [],
                "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    rows = []
    for line in r["text"].strip().split("\n")[1:]:
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        v = num(parts[1])
        if v is not None:
            rows.append({"date": parts[0], "value": v})
    if not rows:
        return {"ok": False, "value": None, "time": None, "history": [],
                "meta": {"status": r["status"], "ms": r["ms"], "error": "no rows"}}
    last = rows[-1]
    try:
        t = int(datetime.strptime(last["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except Exception:
        t = None
    return {"ok": True, "value": last["value"], "time": t, "history": rows[-6:],
            "meta": {"status": r["status"], "ms": r["ms"]}}


# GDELT 文章計數（新聞／敘事詞頻代理；step0 從 VM 429，Actions 出口待驗證）
# timespan=1h 的行為未驗證：若回傳計數異常龐大（>5000，疑似非 1h 區間），視為不可信 → null。
GDELT_QUERIES = {
    "crash": "bitcoin crash OR bitcoin fear OR bitcoin ban",
    "greed": "bitcoin moon OR bitcoin ATH OR bitcoin bull",
    "digitalgold": '"digital gold" bitcoin',
    "safehaven": '"safe haven" gold',
    "ttd": '"this time different" crypto',
    "endcrypto": '"end of crypto"',
    "geo": "war OR missile OR airstrike",
    "regulation": "SEC crypto regulation OR crypto ban",
}


def get_gdelt():
    out = {}
    for key, q in GDELT_QUERIES.items():
        url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
               + urllib.request.quote(q)
               + "&mode=artlist&maxrecords=1&format=json&timespan=1h")
        r = fetch_json(url)
        n = num(r["data"].get("totalArticles")) if (r["ok"] and isinstance(r["data"], dict)) else None
        out[key] = n if (n is not None and n <= 5000) else None
        out[key + "_meta"] = {"status": r["status"], "ms": r["ms"], "error": r.get("error", ""), "raw": n}
    return out


def get_liquidations():
    """edgeornot 強平（Grok 表引用，step0 未列出，未驗證；失敗即 null）"""
    r = fetch_json("https://edgeornot.com/api/liquidations")
    if not r["ok"] or not isinstance(r["data"], dict):
        return {"ok": False, "longUsd": None, "shortUsd": None,
                "meta": {"status": r["status"], "ms": r["ms"], "error": r.get("error", "")}}
    d = r["data"].get("data") if isinstance(r["data"].get("data"), dict) else r["data"]
    long_usd = num(d.get("longLiquidationUsd") or d.get("long_usd") or d.get("long"))
    short_usd = num(d.get("shortLiquidationUsd") or d.get("short_usd") or d.get("short"))
    if long_usd is None and short_usd is None:
        return {"ok": False, "longUsd": None, "shortUsd": None,
                "meta": {"status": r["status"], "ms": r["ms"], "error": "schema unknown"}}
    return {"ok": True, "longUsd": long_usd, "shortUsd": short_usd,
            "meta": {"status": r["status"], "ms": r["ms"]}}

# ---------- 打包：64 行 × 3 標的 = 192 筆（只出原始代理值 x，不算分數、不寫符號） ----------
# raw 項：{ psy, asset, x, source_time, missing_reason, window }
# source_time 為原始資料本身的時間（毫秒 epoch）；缺值時 x=null、source_time=null。
def raw_item(psy, asset, x, source_time, missing_reason="", window="180d"):
    if x is None:
        return {"psy": psy, "asset": asset, "x": None, "source_time": None,
                "missing_reason": missing_reason or MR["FETCH_FAIL"], "window": window}
    return {"psy": psy, "asset": asset, "x": x, "source_time": source_time,
            "missing_reason": "", "window": window}


def null_item(psy, asset, reason, window="180d"):
    return raw_item(psy, asset, None, None, reason, window)


def build_items(D, S):
    items = []

    def push(psy, vals, window="180d"):
        for a in ASSETS:
            v = vals.get(a)
            if isinstance(v, dict) and "x" in v:
                items.append(raw_item(psy, a, v.get("x"), v.get("t"), v.get("mr", ""), window))
            else:
                items.append(null_item(psy, a, (v or {}).get("mr") if isinstance(v, dict) else MR["FETCH_FAIL"], window))

    def same(psy, x, t, mr="", window="180d"):
        for a in ASSETS:
            items.append(raw_item(psy, a, x, t, mr, window))

    def all_null(psy, mr, window="180d"):
        for a in ASSETS:
            items.append(null_item(psy, a, mr, window))

    now_ms = D["nowMs"]
    prev_raw = (S or {}).get("prevRaw") or {}

    # ---- A 組：恐懼／貪婪核心 ----
    # PSY01 FGI（日更；值每日才變，source_time 為 FGI 本身時間戳）
    if D["fgi"]["ok"]:
        same("PSY01", D["fgi"]["value"], D["fgi"]["time"], "", "1d")
    else:
        all_null("PSY01", MR["FETCH_FAIL"] + "（Alternative.me）", "1d")

    # PSY02 資金費率（OKX；XAU 無資金費率）
    push("PSY02", {
        "BTC": {"x": D["funding"]["BTC"]["rate"], "t": D["funding"]["BTC"]["time"]} if D["funding"]["BTC"] else {"mr": MR["FETCH_FAIL"] + "（OKX funding-rate）"},
        "ETH": {"x": D["funding"]["ETH"]["rate"], "t": D["funding"]["ETH"]["time"]} if D["funding"]["ETH"] else {"mr": MR["FETCH_FAIL"] + "（OKX funding-rate）"},
        "XAU": {"mr": "黃金無永續資金費率"},
    })
    # PSY03／04 強平（edgeornot 首次嘗試；Coinglass 無 key 回 500）
    if D["liq"]["ok"]:
        push("PSY03", {"BTC": {"x": D["liq"]["longUsd"], "t": now_ms},
                       "ETH": {"x": D["liq"]["longUsd"], "t": now_ms},
                       "XAU": {"mr": "強平為加密合約指標，不適用黃金"}})
        push("PSY04", {"BTC": {"x": D["liq"]["shortUsd"], "t": now_ms},
                       "ETH": {"x": D["liq"]["shortUsd"], "t": now_ms},
                       "XAU": {"mr": "強平為加密合約指標，不適用黃金"}})
    else:
        all_null("PSY03", "強平來源須 API key（Coinglass 無 key 回 500）；edgeornot 首次嘗試失敗")
        all_null("PSY04", "強平來源須 API key（Coinglass 無 key 回 500）；edgeornot 首次嘗試失敗")
    # PSY05 OI 1h 變化率（需上一小時原始 OI；暖機第一包為 null）
    def oi_change(sym):
        cur = D["oi"].get(sym)
        prev = (prev_raw.get("oi") or {}).get(sym)
        if not cur or prev is None or prev == 0:
            return {"mr": MR["WARMUP"] if cur else MR["FETCH_FAIL"] + "（OKX open-interest）"}
        return {"x": (cur["oi"] - prev) / prev, "t": cur["time"]}

    push("PSY05", {"BTC": oi_change("BTC"), "ETH": oi_change("ETH"), "XAU": {"mr": "黃金無永續 OI"}})
    # PSY06 多空比（OKX rubik，未驗證端點；失敗即 null）
    push("PSY06", {
        "BTC": {"x": D["lsr"]["BTC"], "t": now_ms} if D["lsr"]["BTC"] is not None else {"mr": "OKX long-short ratio 端點未驗證／抓取失敗；Binance 多空比地理封鎖（451）"},
        "ETH": {"x": D["lsr"]["ETH"], "t": now_ms} if D["lsr"]["ETH"] is not None else {"mr": "OKX long-short ratio 端點未驗證／抓取失敗；Binance 多空比地理封鎖（451）"},
        "XAU": {"mr": "黃金無多空比"},
    })
    # PSY07 1h 實現波動率（OKX K 線自算；XAU 用 CoinGecko 小時價）
    def rv(closes):
        rets = log_returns(closes)
        if len(rets) < 12:
            return None
        return stdev(rets[-24:])

    candles_btc = D["candles"]["BTC"]
    rv_t = candles_btc["candles"][-1]["t"] if (candles_btc["ok"] and candles_btc["candles"]) else None
    push("PSY07", {
        "BTC": {"x": rv([c["c"] for c in D["candles"]["BTC"]["candles"]]), "t": rv_t} if D["candles"]["BTC"]["ok"] else {"mr": MR["FETCH_FAIL"] + "（OKX candles）"},
        "ETH": {"x": rv([c["c"] for c in D["candles"]["ETH"]["candles"]]), "t": rv_t} if D["candles"]["ETH"]["ok"] else {"mr": MR["FETCH_FAIL"] + "（OKX candles）"},
        "XAU": {"x": rv([p["p"] for p in D["hourly"]["XAU"]["prices"]]), "t": now_ms} if D["hourly"]["XAU"]["ok"] else {"mr": MR["FETCH_FAIL"] + "（CoinGecko XAUt 小時價）"},
    })
    # PSY08 期權 25Δ 偏態（Deribit 期權鏈遍歷，最佳努力；XAU 無）
    push("PSY08", {
        "BTC": {"x": D["skew"]["BTC"]["skew"], "t": now_ms} if D["skew"]["BTC"]["ok"] else {"mr": "Deribit 25Δ 偏態未取到（行權價／delta 缺失或逾時）"},
        "ETH": {"x": D["skew"]["ETH"]["skew"], "t": now_ms} if D["skew"]["ETH"]["ok"] else {"mr": "Deribit 25Δ 偏態未取到（行權價／delta 缺失或逾時）"},
        "XAU": {"mr": "黃金無 Deribit 期權偏態"},
    })

    # ---- B 組：從眾／社群／注意力 ----
    all_null("PSY09", MR["PAYWALL"] + "（LunarCrush／The Tie 社群情緒極性）")
    all_null("PSY10", MR["PAYWALL"] + "（LunarCrush／The Tie 社群提及量）")
    # PSY11／12 新聞詞頻（GDELT 文章計數代理；timespan=1h 行為未驗證）
    if D["gdelt"].get("crash") is not None:
        same("PSY11", D["gdelt"]["crash"], now_ms)
    else:
        all_null("PSY11", "GDELT 抓取失敗／計數不可信（step0 從 VM 429）")
    if D["gdelt"].get("greed") is not None:
        same("PSY12", D["gdelt"]["greed"], now_ms)
    else:
        all_null("PSY12", "GDELT 抓取失敗／計數不可信（step0 從 VM 429）")
    all_null("PSY13", "無穩定每小時公開 API（Google Trends 429）", "1d")
    all_null("PSY14", "無穩定每小時公開 API（Google Trends 429）", "1d")
    # PSY15／16 敘事提及（GDELT 文章計數代理）
    if D["gdelt"].get("digitalgold") is not None:
        same("PSY15", D["gdelt"]["digitalgold"], now_ms)
    else:
        all_null("PSY15", "GDELT 抓取失敗／計數不可信")
    if D["gdelt"].get("safehaven") is not None:
        same("PSY16", D["gdelt"]["safehaven"], now_ms)
    else:
        all_null("PSY16", "GDELT 抓取失敗／計數不可信")

    # ---- C 組：錨定／參考點（價格自算） ----
    def closes_of(sym):
        c = D["candles"].get(sym)
        return [x["c"] for x in c["candles"]] if (c and c["ok"]) else None

    def highs24(sym):
        c = D["candles"].get(sym)
        return [x["h"] for x in c["candles"][-24:]] if (c and c["ok"]) else None

    def lows24(sym):
        c = D["candles"].get(sym)
        return [x["l"] for x in c["candles"][-24:]] if (c and c["ok"]) else None

    xau_hourly = [p["p"] for p in D["hourly"]["XAU"]["prices"]] if D["hourly"]["XAU"]["ok"] else None

    def last_price(sym):
        if sym == "XAU":
            return xau_hourly[-1] if xau_hourly else None
        c = closes_of(sym)
        return c[-1] if c else None

    def anchor_row(psy, fn):
        vals = {}
        for sym, closes, highs, lows in (
            ("BTC", closes_of("BTC"), highs24("BTC"), lows24("BTC")),
            ("ETH", closes_of("ETH"), highs24("ETH"), lows24("ETH")),
            ("XAU", xau_hourly, xau_hourly[-24:] if xau_hourly else None,
             xau_hourly[-24:] if xau_hourly else None),
        ):
            v = fn(sym, closes, highs, lows)
            if v is None:
                vals[sym] = {"mr": MR["FETCH_FAIL"] + ("（CoinGecko XAUt 小時價）" if sym == "XAU" else "（OKX candles）")}
            else:
                vals[sym] = {"x": v, "t": now_ms if sym == "XAU" else rv_t}
        push(psy, vals)

    # PSY17 前高錨定距離 (P−H24)/H24；PSY18 前低 (P−L24)/L24
    def _p17(sym, closes, highs, lows):
        if not closes or not highs:
            return None
        h = max(highs)
        return (closes[-1] - h) / h if h else None

    def _p18(sym, closes, highs, lows):
        if not closes or not lows:
            return None
        lo = min(lows)
        return (closes[-1] - lo) / lo if lo else None

    anchor_row("PSY17", _p17)
    anchor_row("PSY18", _p18)
    # PSY19 整數關卡錨定：|P−錨點|／錨點（BTC 每 1000、ETH 每 100、XAU 每 50）
    def _p19(sym, closes, highs, lows):
        p = last_price(sym)
        if p is None:
            return None
        step = 1000 if sym == "BTC" else (100 if sym == "ETH" else 50)
        anchor = round(p / step) * step
        return abs(p - anchor) / anchor if anchor else None

    anchor_row("PSY19", _p19)
    # PSY20 52 週高點錨定 ln(P／H365)；PSY21 均值回歸偏離 Z；PSY23 技術突破（+1／0／−1）
    def daily_closes(sym):
        d = D["daily"].get(sym)
        return [p["p"] for p in d["prices"]] if (d and d["ok"]) else None

    def breakout(sym):
        dc = daily_closes(sym)
        if not dc or len(dc) < 31:
            return None
        p = dc[-1]
        h30 = max(dc[-31:-1])
        l30 = min(dc[-31:-1])
        if p > h30:
            return 1
        if p < l30:
            return -1
        return 0

    def _p20(sym):
        dc = daily_closes(sym)
        if not dc:
            return None
        return math.log(dc[-1] / max(dc))

    push("PSY20", {sym: ({"x": _p20(sym), "t": now_ms} if _p20(sym) is not None
                          else {"mr": MR["FETCH_FAIL"] + "（CoinGecko 365d）"}) for sym in ASSETS})

    def _p21(sym):
        dc = daily_closes(sym)
        if not dc or len(dc) < 31:
            return None
        ma = mean(dc[-31:-1])
        sd = stdev(dc[-31:-1])
        if ma is None or not sd:
            return None
        return (dc[-1] - ma) / sd

    push("PSY21", {sym: ({"x": _p21(sym), "t": now_ms} if _p21(sym) is not None
                          else {"mr": MR["FETCH_FAIL"] + "（CoinGecko 365d，日數不足）"}) for sym in ASSETS})
    # PSY22 動量自相關 ρ（24 根 1h 報酬）；PSY24 近因 r1／r24
    def hourly_prices(sym):
        return xau_hourly if sym == "XAU" else closes_of(sym)

    def _rho(sym):
        rets = log_returns(hourly_prices(sym) or [])
        return autocorr1(rets[-24:])

    push("PSY22", {sym: ({"x": _rho(sym), "t": now_ms if sym == "XAU" else rv_t} if _rho(sym) is not None
                          else {"mr": MR["FETCH_FAIL"] + "（小時報酬不足 24 根）"}) for sym in ASSETS})
    push("PSY23", {sym: ({"x": breakout(sym), "t": now_ms} if breakout(sym) is not None
                          else {"mr": MR["FETCH_FAIL"] + "（CoinGecko 365d，日數不足）"}) for sym in ASSETS})

    def _p24(sym):
        p = hourly_prices(sym)
        if not p or len(p) < 25:
            return None
        r1 = p[-1] / p[-2] - 1
        r24 = p[-1] / p[-25] - 1
        if r24 == 0:
            return None
        return math.copysign(min(abs(r1) / abs(r24), 1), r1)

    push("PSY24", {sym: ({"x": _p24(sym), "t": now_ms if sym == "XAU" else rv_t} if _p24(sym) is not None
                          else {"mr": MR["FETCH_FAIL"] + "（小時價不足 25 根）"}) for sym in ASSETS})

    # ---- D 組：損失／處置／持有 ----
    all_null("PSY25", MR["PAYWALL"] + "（Glassnode SOPR，1h 須付費）")
    all_null("PSY26", MR["PAYWALL"] + "（鏈上已實現損益）")
    all_null("PSY27", MR["PAYWALL"] + "（鏈上已實現損益）")
    all_null("PSY28", "無每小時來源（Glassnode HODL waves 日更為主，須付費）", "1d")
    all_null("PSY29", "無每小時來源（Glassnode MVRV 日更為主，須付費）", "1d")
    all_null("PSY30", "CryptoQuant 403，交易所淨流入免費層未驗證可用")
    all_null("PSY31", "CryptoQuant 403，交易所淨流出免費層未驗證可用")
    all_null("PSY32", MR["PAYWALL"] + "（Whale Alert 鯨魚轉帳須 API key）")
    return items, {"xau_hourly": xau_hourly, "rv_t": rv_t}

def build_items_eh(D, S, ctx, items):
    """E–H 組（續 build_items；ctx 為 build_items 回傳的 {xau_hourly, rv_t}）。"""
    now_ms = D["nowMs"]
    prev_raw = (S or {}).get("prevRaw") or {}
    xau_hourly = ctx["xau_hourly"]
    rv_t = ctx["rv_t"]

    def push(psy, vals, window="180d"):
        for a in ASSETS:
            v = vals.get(a)
            if isinstance(v, dict) and "x" in v:
                items.append(raw_item(psy, a, v.get("x"), v.get("t"), v.get("mr", ""), window))
            else:
                items.append(null_item(psy, a, (v or {}).get("mr") if isinstance(v, dict) else MR["FETCH_FAIL"], window))

    def same(psy, x, t, mr="", window="180d"):
        for a in ASSETS:
            items.append(raw_item(psy, a, x, t, mr, window))

    def all_null(psy, mr, window="180d"):
        for a in ASSETS:
            items.append(null_item(psy, a, mr, window))

    def closes_of(sym):
        c = D["candles"].get(sym)
        return [x["c"] for x in c["candles"]] if (c and c["ok"]) else None

    # ---- E 組：偏誤組合 ----
    # PSY33 過度自信槓桿：資金費率絕對值（極端值代理）
    push("PSY33", {
        "BTC": {"x": abs(D["funding"]["BTC"]["rate"]), "t": D["funding"]["BTC"]["time"]} if D["funding"]["BTC"] else {"mr": MR["FETCH_FAIL"] + "（OKX funding-rate）"},
        "ETH": {"x": abs(D["funding"]["ETH"]["rate"]), "t": D["funding"]["ETH"]["time"]} if D["funding"]["ETH"] else {"mr": MR["FETCH_FAIL"] + "（OKX funding-rate）"},
        "XAU": {"mr": "黃金無資金費率"},
    })
    # PSY34／35／36 主動買賣比（OKX rubik taker-volume，未驗證端點）
    def taker_x(sym, side):
        tk = D["taker"].get(sym)
        if not tk:
            return {"mr": "OKX taker-volume 端點未驗證／抓取失敗；Binance aggTrades 地理封鎖（451）"}
        tot = tk["buy"] + tk["sell"]
        if not tot:
            return {"mr": MR["FETCH_FAIL"] + "（taker-volume 總量為 0）"}
        return {"x": tk["buy"] / tot if side == "buy" else tk["sell"] / tot, "t": now_ms}

    push("PSY34", {"BTC": taker_x("BTC", "buy"), "ETH": taker_x("ETH", "buy"), "XAU": {"mr": "黃金無 taker buy-sell"}})
    push("PSY35", {"BTC": taker_x("BTC", "sell"), "ETH": taker_x("ETH", "sell"), "XAU": {"mr": "黃金無 taker buy-sell"}})
    push("PSY36", {"BTC": taker_x("BTC", "sell"), "ETH": taker_x("ETH", "sell"), "XAU": {"mr": "黃金無 taker buy-sell"}})
    # PSY37 合約量占比（OKX 合約 K 線量／（現貨＋合約）量）
    def contract_share(sym):
        sw = D["candles"].get(sym)
        sp = D["spotCandles"].get(sym)
        if not (sw and sw["ok"] and sw["candles"] and sp and sp["ok"] and sp["candles"]):
            return {"mr": MR["FETCH_FAIL"] + "（OKX 合約／現貨 K 線）"}
        sv, pv = sw["candles"][-1]["v"], sp["candles"][-1]["v"]
        if sv is None or pv is None or sv + pv == 0:
            return {"mr": MR["FETCH_FAIL"] + "（成交量為空）"}
        return {"x": sv / (sv + pv), "t": rv_t}

    push("PSY37", {"BTC": contract_share("BTC"), "ETH": contract_share("ETH"), "XAU": {"mr": "黃金無合約／現貨量比"}})
    all_null("PSY38", MR["NO_ENDPOINT"] + "（OI 期限結構無公開端點）")
    # PSY39 刪除候選（對照組）：仍出現在 bundle，x 為 null（需 NLP，未部署）
    all_null("PSY39", MR["NLP_MISSING"] + "（後見之明詞頻；刪除候選，對照組，定義保留）")
    # PSY40 刪除候選（對照組）：成交量異常低可自算，x 照常出（計分與否由下游檢定決定）
    def low_vol(sym):
        cs = D["candles"].get(sym)
        if not (cs and cs["ok"] and len(cs["candles"]) >= 25):
            return None
        vols = [c["v"] for c in cs["candles"][-25:-1]]
        med = median(vols)
        last = cs["candles"][-1]["v"]
        return last / med if med else None

    def low_vol_xau():
        vs = D["hourly"]["XAU"]["volumes"] if D["hourly"]["XAU"]["ok"] else []
        if len(vs) < 25:
            return None
        med = median([x["v"] for x in vs[-25:-1]])
        last = vs[-1]["v"]
        return last / med if med else None

    push("PSY40", {
        "BTC": {"x": low_vol("BTC"), "t": rv_t} if low_vol("BTC") is not None else {"mr": MR["FETCH_FAIL"] + "（OKX candles 量不足）"},
        "ETH": {"x": low_vol("ETH"), "t": rv_t} if low_vol("ETH") is not None else {"mr": MR["FETCH_FAIL"] + "（OKX candles 量不足）"},
        "XAU": {"x": low_vol_xau(), "t": now_ms} if low_vol_xau() is not None else {"mr": MR["FETCH_FAIL"] + "（CoinGecko 量不足）"},
    })

    # ---- F 組：敘事／信念／框架 ----
    all_null("PSY41", MR["PAYWALL"] + "（社群同溫層，須社群 API）")
    if D["gdelt"].get("ttd") is not None:
        same("PSY42", D["gdelt"]["ttd"], now_ms)
    else:
        all_null("PSY42", "GDELT 抓取失敗／計數不可信")
    if D["gdelt"].get("endcrypto") is not None:
        same("PSY43", D["gdelt"]["endcrypto"], now_ms)
    else:
        all_null("PSY43", "GDELT 抓取失敗／計數不可信")
    all_null("PSY44", "X 追蹤列表須人工／API，無自動事件源")
    all_null("PSY45", MR["EVENT_ONLY"] + "（機構研報發布，無自動事件源）")
    all_null("PSY46", MR["NLP_MISSING"] + "（稀缺敘事提及頻率）")
    all_null("PSY47", MR["PAYWALL"] + "（空投提及，社群來源付費牆）")
    all_null("PSY48", "人工／半自動追蹤，無自動源（刪除候選，定義保留）")

    # ---- G 組：社會／賭性／反應 ----
    all_null("PSY49", "複合指標需社群提及同步，社群來源付費牆")
    all_null("PSY50", "複合指標需強平數據，強平來源須 API key")
    all_null("PSY51", "需新聞時間戳＋價格振幅，新聞源未部署")
    all_null("PSY52", MR["PAYWALL"] + "（鏈上活躍地址須付費）")
    # PSY53 賭徒謬誤／PSY54 熱手謬誤：同一連漲小時數（下游符號相反，不在此預設）
    def streak_row(psy):
        def val(sym):
            p = xau_hourly if sym == "XAU" else closes_of(sym)
            if not p or len(p) < 2:
                return {"mr": MR["FETCH_FAIL"]}
            return {"x": up_streak(p), "t": now_ms if sym == "XAU" else rv_t}
        push(psy, {sym: val(sym) for sym in ASSETS})

    streak_row("PSY53")
    streak_row("PSY54")
    all_null("PSY55", MR["PAYWALL"] + "（極端情緒帖占比，須社群 API）")
    all_null("PSY56", MR["PAYWALL"] + "（鏈上跟單，須鏈上數據）")

    # ---- H 組：宏觀／避險／跨資產 ----
    # PSY57 黃金 1h 漲跌幅（同一 r金，三標的皆出；下游符號處理）
    gold_ret = None
    if xau_hourly and len(xau_hourly) >= 2:
        gold_ret = xau_hourly[-1] / xau_hourly[-2] - 1
    if gold_ret is not None:
        same("PSY57", gold_ret, now_ms)
    else:
        all_null("PSY57", MR["FETCH_FAIL"] + "（CoinGecko XAUt 小時價）")
    all_null("PSY58", MR["NO_ENDPOINT"] + "（DXY 無公開每小時端點，TradingView 無公開 API）")
    # PSY59 實質利率：FRED DFII10 5 日變動（需歷史，state 累積；暖機 null）
    if D["fred"]["ok"]:
        hist = [v for v in list(prev_raw.get("fredHistory") or []) + [D["fred"]["value"]]
                if v is not None]
        if len(hist) >= 6:
            same("PSY59", D["fred"]["value"] - hist[-6], D["fred"]["time"], "", "1d")
        else:
            all_null("PSY59", MR["WARMUP"] + "（FRED 5 日變動需 6 筆日值累積）", "1d")
    else:
        all_null("PSY59", "FRED 從本 VM 逾時，Actions 出口待驗證", "1d")
    if D["gdelt"].get("geo") is not None:
        same("PSY60", D["gdelt"]["geo"], now_ms)
    else:
        all_null("PSY60", "GDELT 抓取失敗／計數不可信")
    all_null("PSY61", "多無每小時來源（黃金 ETF 流入／GVZ 部分日更）", "1d")
    if D["gdelt"].get("regulation") is not None:
        same("PSY62", D["gdelt"]["regulation"], now_ms)
    else:
        all_null("PSY62", "GDELT 抓取失敗／計數不可信")
    all_null("PSY63", "日更為主，須付費（CryptoQuant／Nansen）", "1d")
    # PSY64 穩定幣市值（USDT＋USDC 合計，日更節奏）
    if D["stable"]["ok"] and D["stable"]["mcap"] is not None:
        same("PSY64", D["stable"]["mcap"], now_ms, "", "1d")
    else:
        all_null("PSY64", MR["FETCH_FAIL"] + "（CoinGecko 穩定幣市值）", "1d")
    return items


# ---------- 完成 bundle：補 last_valid_time／stale_age_h／is_duplicate ----------
def finalize(items, bundle_time_ms, prev_items):
    out = []
    for it in items:
        key = f"{it['psy']}_{it['asset']}"
        prev = (prev_items or {}).get(key)
        last_valid = None
        if it["x"] is not None:
            last_valid = it["source_time"]
        elif prev and prev.get("last_valid_time"):
            last_valid = prev["last_valid_time"]
        stale = None
        if last_valid is not None:
            stale = max(0, int((bundle_time_ms - last_valid) // 3600000))
        dup = (it["x"] is not None and prev and prev.get("x") is not None
               and prev["x"] == it["x"])
        out.append({
            "psy": it["psy"], "asset": it["asset"], "x": it["x"],
            "source_time": taipei_iso_from_ms(it["source_time"]),
            "fetch_time": taipei_iso_from_ms(bundle_time_ms),
            "last_valid_time": taipei_iso_from_ms(last_valid),
            "window": it["window"], "stale_age_h": stale,
            "is_duplicate": bool(dup), "missing_reason": it["missing_reason"] or "",
        })
    return out


# ---------- 狀態讀寫（上一包 bundle.json＋state.json，皆在 repo 內） ----------
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_prev_state():
    state = {"prevRaw": {}, "prevItems": {}}
    prev_bundle = load_json(BASE / "bundle.json")
    if prev_bundle and isinstance(prev_bundle.get("items"), list):
        for it in prev_bundle["items"]:
            try:
                lv = None
                if it.get("last_valid_time"):
                    lv = int(datetime.strptime(it["last_valid_time"], "%Y-%m-%dT%H:%M:%S+08:00")
                             .replace(tzinfo=TAIPEI).timestamp() * 1000)
            except Exception:
                lv = None
            state["prevItems"][f"{it['psy']}_{it['asset']}"] = {"x": it.get("x"), "last_valid_time": lv}
    prev_state = load_json(BASE / "state.json")
    if prev_state and isinstance(prev_state.get("prevRaw"), dict):
        state["prevRaw"] = prev_state["prevRaw"]
    return state

# ---------- 每小時執行 ----------
def run_hourly():
    now = taipei_now()
    bundle_dt = hour_floor(now)
    bundle_time_ms = int(bundle_dt.timestamp() * 1000)
    bundle_time = taipei_iso(bundle_dt)
    hour = hour_key(bundle_dt)
    meta = {"sources": {}, "errors": []}

    state = load_prev_state()

    # 並行拉取第一批（CoinGecko 免費層限速：組內循序、間隔 300ms）
    with ThreadPoolExecutor(max_workers=12) as ex:
        jobs = {
            "funding": ex.submit(get_funding),
            "oi": ex.submit(get_open_interest),
            "candlesBTC": ex.submit(get_candles, "BTC-USDT-SWAP"),
            "candlesETH": ex.submit(get_candles, "ETH-USDT-SWAP"),
            "spotBTC": ex.submit(get_candles, "BTC-USDT"),
            "spotETH": ex.submit(get_candles, "ETH-USDT"),
            "dvol": ex.submit(get_dvol),
            "fgi": ex.submit(get_fgi),
            "fred": ex.submit(get_fred),
            "gdelt": ex.submit(get_gdelt),
            "liq": ex.submit(get_liquidations),
        }
        res = {k: fut.result() for k, fut in jobs.items()}
    with ThreadPoolExecutor(max_workers=4) as ex:
        jobs2 = {
            "lsrBTC": ex.submit(get_rubik, "lsr", "BTC-USDT-SWAP"),
            "lsrETH": ex.submit(get_rubik, "lsr", "ETH-USDT-SWAP"),
            "takerBTC": ex.submit(get_rubik, "taker", "BTC-USDT-SWAP"),
            "takerETH": ex.submit(get_rubik, "taker", "ETH-USDT-SWAP"),
        }
        res2 = {k: fut.result() for k, fut in jobs2.items()}

    cg_simple = get_cg_simple()
    sleep(0.3)
    cg_hourly_btc = get_cg_chart("bitcoin", 2, "hourly")
    sleep(0.3)
    cg_hourly_eth = get_cg_chart("ethereum", 2, "hourly")
    sleep(0.3)
    cg_hourly_xau = get_cg_chart("tether-gold", 2, "hourly")
    sleep(0.3)
    cg_daily_btc = get_cg_chart("bitcoin", 365, "daily")
    sleep(0.3)
    cg_daily_eth = get_cg_chart("ethereum", 365, "daily")
    sleep(0.3)
    cg_daily_xau = get_cg_chart("tether-gold", 365, "daily")
    sleep(0.3)
    cg_stable = get_cg_stable()

    # Deribit 偏態（需現價定行權價範圍；用 OKX 收盤價，否則用 CoinGecko 現價）
    def last_close(candles_res, cg_price):
        if candles_res["ok"] and candles_res["candles"]:
            return candles_res["candles"][-1]["c"]
        return cg_price

    price_btc = last_close(res["candlesBTC"], cg_simple["prices"].get("BTC") if cg_simple["ok"] else None)
    price_eth = last_close(res["candlesETH"], cg_simple["prices"].get("ETH") if cg_simple["ok"] else None)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_btc = ex.submit(get_skew, "BTC", price_btc) if price_btc else None
        f_eth = ex.submit(get_skew, "ETH", price_eth) if price_eth else None
        skew_btc = f_btc.result() if f_btc else {"ok": False, "skew": None, "meta": {"error": "no spot"}}
        skew_eth = f_eth.result() if f_eth else {"ok": False, "skew": None, "meta": {"error": "no spot"}}

    D = {
        "nowMs": bundle_time_ms,
        "funding": res["funding"], "oi": res["oi"],
        "candles": {"BTC": res["candlesBTC"], "ETH": res["candlesETH"]},
        "spotCandles": {"BTC": res["spotBTC"], "ETH": res["spotETH"]},
        "lsr": {
            "BTC": num(res2["lsrBTC"]["row"].get("longShortRatio")) if res2["lsrBTC"]["ok"] else None,
            "ETH": num(res2["lsrETH"]["row"].get("longShortRatio")) if res2["lsrETH"]["ok"] else None,
        },
        "taker": {
            "BTC": {"buy": num(res2["takerBTC"]["row"].get("buyVol")),
                    "sell": num(res2["takerBTC"]["row"].get("sellVol"))} if res2["takerBTC"]["ok"] else None,
            "ETH": {"buy": num(res2["takerETH"]["row"].get("buyVol")),
                    "sell": num(res2["takerETH"]["row"].get("sellVol"))} if res2["takerETH"]["ok"] else None,
        },
        "dvol": res["dvol"],
        "skew": {"BTC": skew_btc, "ETH": skew_eth},
        "cgSimple": cg_simple,
        "hourly": {"BTC": cg_hourly_btc, "ETH": cg_hourly_eth, "XAU": cg_hourly_xau},
        "daily": {"BTC": cg_daily_btc, "ETH": cg_daily_eth, "XAU": cg_daily_xau},
        "stable": cg_stable,
        "fgi": res["fgi"], "fred": res["fred"], "gdelt": res["gdelt"], "liq": res["liq"],
    }

    # 來源狀態彙總（寫進 bundle.meta，不影響分數）
    def src_status(name, ok, m):
        meta["sources"][name] = {"ok": bool(ok), "status": (m or {}).get("status"),
                                 "ms": (m or {}).get("ms"), "error": (m or {}).get("error", "")}

    src_status("okx_funding", bool(D["funding"]["BTC"] or D["funding"]["ETH"]), D["funding"].get("BTC_meta"))
    src_status("okx_oi", bool(D["oi"]["BTC"] or D["oi"]["ETH"]), D["oi"].get("BTC_meta"))
    src_status("okx_candles", D["candles"]["BTC"]["ok"], D["candles"]["BTC"]["meta"])
    src_status("okx_rubik_lsr", res2["lsrBTC"]["ok"], res2["lsrBTC"]["meta"])
    src_status("okx_rubik_taker", res2["takerBTC"]["ok"], res2["takerBTC"]["meta"])
    src_status("deribit_dvol", bool(D["dvol"]["BTC"]), D["dvol"].get("BTC_meta"))
    src_status("deribit_skew_btc", skew_btc["ok"], skew_btc["meta"])
    src_status("coingecko_simple", cg_simple["ok"], cg_simple["meta"])
    src_status("coingecko_hourly", cg_hourly_btc["ok"], cg_hourly_btc["meta"])
    src_status("coingecko_daily365", cg_daily_btc["ok"], cg_daily_btc["meta"])
    src_status("coingecko_stable", cg_stable["ok"], cg_stable["meta"])
    src_status("alternative_fgi", res["fgi"]["ok"], res["fgi"]["meta"])
    src_status("fred_dfii10", res["fred"]["ok"], res["fred"]["meta"])
    src_status("gdelt", D["gdelt"].get("crash") is not None, D["gdelt"].get("crash_meta"))
    src_status("edgeornot_liq", res["liq"]["ok"], res["liq"]["meta"])

    raw_items, ctx = build_items(D, state)
    raw_items = build_items_eh(D, state, ctx, raw_items)
    items = finalize(raw_items, bundle_time_ms, state["prevItems"])

    bundle = {"bundle_time": bundle_time, "items": items, "meta": meta}
    bundle_text = json.dumps(bundle, ensure_ascii=False)

    # 寫出：最新一包＋歷史 append-only＋state
    (BASE / "bundle.json").write_text(bundle_text, encoding="utf-8")
    hist_dir = BASE / "history"
    hist_dir.mkdir(exist_ok=True)
    (hist_dir / f"{hour}.json").write_text(bundle_text, encoding="utf-8")
    prune_history(hist_dir)

    prev_items = {}
    for it in items:
        lv = None
        if it["last_valid_time"]:
            try:
                lv = int(datetime.strptime(it["last_valid_time"], "%Y-%m-%dT%H:%M:%S+08:00")
                         .replace(tzinfo=TAIPEI).timestamp() * 1000)
            except Exception:
                lv = None
        prev_items[f"{it['psy']}_{it['asset']}"] = {"x": it["x"], "last_valid_time": lv}
    fred_history = list(state["prevRaw"].get("fredHistory") or [])
    if res["fred"]["ok"]:
        fred_history = [v for v in fred_history + [res["fred"]["value"]] if v is not None][-10:]
    new_state = {
        "prevRaw": {
            "oi": {
                "BTC": D["oi"]["BTC"]["oi"] if D["oi"]["BTC"] else (state["prevRaw"].get("oi") or {}).get("BTC"),
                "ETH": D["oi"]["ETH"]["oi"] if D["oi"]["ETH"] else (state["prevRaw"].get("oi") or {}).get("ETH"),
            },
            "fredHistory": fred_history,
        },
        "prevItems": prev_items,
        "updated_at": bundle_time,
    }
    (BASE / "state.json").write_text(json.dumps(new_state, ensure_ascii=False), encoding="utf-8")

    n_null = sum(1 for i in items if i["x"] is None)
    print(f"[psy64] bundle {bundle_time} items={len(items)} null={n_null} bytes={len(bundle_text.encode('utf-8'))}")
    return {"bundle_time": bundle_time, "items": len(items), "nulls": n_null}


def prune_history(hist_dir):
    """刪除 45 天以前的歷史包（檔名即台北小時 YYYY-MM-DDTHH）。"""
    cutoff = (taipei_now() - timedelta(days=HISTORY_KEEP_DAYS)).strftime("%Y-%m-%dT%H")
    for p in hist_dir.glob("*.json"):
        if p.stem < cutoff:
            try:
                p.unlink()
                print(f"[psy64] pruned {p.name}")
            except Exception as e:
                print(f"[psy64] prune failed {p.name}: {e}", file=sys.stderr)


def main():
    result = run_hourly()
    if result["items"] != 192:
        print(f"[psy64] WARNING: items={result['items']}（預期 192）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
