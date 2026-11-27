/**
 * psy64-relay — 心理64 Cloudflare Worker 中繼
 *
 * 定稿：consult-20261127-psych64/verdict.md §6（64 行凍結定義）、§8（證偽門檻）
 * 契約：apps-script/factor-fleet/Psy64.gs 開頭註解（中繼 JSON 契約）
 * 實測：consult-20261127-psych64/step0-endpoint-check.md（34 端點，2026-11-27）
 *
 * 主人方法論（2026-11-27 11:24 立規，全程約束）：
 *  1. 64 條全數上線（定稿 §9.3「只做試點 10 個」已被主人 11:26 推翻）。
 *  2. 沒數據產生前，任何一條線都不可先標成噪音、刪掉或降權；刪除候選
 *     3 行（PSY39、PSY40、PSY48）照樣出現在 bundle 裡，缺值寫 null。
 *  3. 不准預設立場：Worker 只出原始代理值 x，絕不在這裡算情緒分數、
 *     不寫死符號、權重或因果。分數由 Psy64.gs 按定稿 §6.0 統一式算；
 *     領先／落後、黃金是否反轉，一律由數據事後判定。
 *  4. 心理學是統計學、市場是考古學；策略邏輯只寫順勢，不寫逆勢抄底。
 *
 * 運作：
 *  - scheduled（cron 每小時整點）：拉取全部可達來源 → 打包成一份輕量 JSON
 *    → 存 KV `bundle.json`（最新一包）＋ `bundle-history/<台北小時>`（append-only，
 *    45 天 TTL）＋ `state.json`（上一小時原始值，供變化率與 last_valid_time 用）。
 *  - fetch：GET /bundle.json 回傳最新一包；GET /health 回傳狀態。
 *  - 缺值一律寫 null 並填 missing_reason，絕不以 0 代替（0 是中性觀測，不是缺失）。
 *  - 單一來源失敗不拖垮整包：該面向出 null＋missing_reason，其餘照常。
 *  - Binance 全線 451 地理封鎖（step0）：Worker 內不依賴 Binance；
 *    PSY06 多空比、PSY36 主動賣出比先出 null，待 OKX rubik 端點驗證或非封鎖出口。
 *  - 付費牆來源（Glassnode、Coinglass、CryptoQuant、LunarCrush、The Tie、
 *    Santiment、Whale Alert、Nansen）一律出 null，不硬撐、不編數字。
 */

const TIMEOUT_MS = 8000;          // 單一來源逾時（毫秒）
const HISTORY_TTL_S = 45 * 24 * 3600; // 歷史包保留 45 天
const ASSETS = ['BTC', 'ETH', 'XAU'];

// ---------- 時間（台北） ----------
function pad(n) { return String(n).padStart(2, '0'); }
function taipeiParts(d) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  });
  const p = {};
  for (const { type, value } of fmt.formatToParts(d)) p[type] = value;
  return p;
}
function taipeiISO(d) {
  const p = taipeiParts(d);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}+08:00`;
}
function taipeiHourKey(d) {
  const p = taipeiParts(d);
  return `${p.year}-${p.month}-${p.day}T${p.hour}`;
}
function hourFloor(d) {
  const p = taipeiParts(d);
  return new Date(`${p.year}-${p.month}-${p.day}T${p.hour}:00:00+08:00`);
}

// ---------- 通用抓取 ----------
async function fetchJSON(url, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || TIMEOUT_MS);
  const t0 = Date.now();
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { 'User-Agent': 'psy64-relay/1.0 (+psych64)', Accept: 'application/json', ...(opts.headers || {}) },
    });
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch { /* 非 JSON，見 fetchText */ }
    return { ok: res.ok, status: res.status, data, text, ms: Date.now() - t0 };
  } catch (e) {
    return { ok: false, status: 0, data: null, text: '', ms: Date.now() - t0, error: String(e && e.name === 'AbortError' ? 'timeout' : e) };
  } finally {
    clearTimeout(timer);
  }
}
async function fetchText(url, opts = {}) {
  const r = await fetchJSON(url, opts);
  return r;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- 數值小工具（只算原始代理值，不算情緒分數） ----------
function num(v) { const n = Number(v); return Number.isFinite(n) ? n : null; }
function logReturns(prices) {
  const out = [];
  for (let i = 1; i < prices.length; i++) {
    if (prices[i - 1] > 0 && prices[i] > 0) out.push(Math.log(prices[i] / prices[i - 1]));
  }
  return out;
}
function mean(a) { return a.length ? a.reduce((s, v) => s + v, 0) / a.length : null; }
function stdev(a) {
  if (a.length < 2) return null;
  const m = mean(a);
  return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - 1));
}
function median(a) {
  if (!a.length) return null;
  const s = [...a].sort((x, y) => x - y);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}
function autocorr1(rets) {
  if (rets.length < 3) return null;
  const m = mean(rets);
  let num = 0, den = 0;
  for (let i = 1; i < rets.length; i++) num += (rets[i] - m) * (rets[i - 1] - m);
  for (const v of rets) den += (v - m) ** 2;
  return den === 0 ? null : num / den;
}
function upStreak(prices) {
  let n = 0;
  for (let i = prices.length - 1; i > 0; i--) {
    if (prices[i] > prices[i - 1]) n++; else break;
  }
  return n;
}

// ---------- 缺值原因（統一字串，寫進 missing_reason） ----------
const MR = {
  NO_HOURLY: '無每小時公開來源',
  PAYWALL: '須 API key／付費牆，免費層無每小時數據',
  GEO_BINANCE: 'Binance 地理封鎖（451），Worker 不依賴 Binance',
  WARMUP: '暖機：無上一小時原始值，無法算變化率',
  FETCH_FAIL: '來源抓取失敗（HTTP 非 200／逾時／解析失敗）',
  EVENT_ONLY: '事件驅動，無自動事件源',
  NLP_MISSING: '需 NLP／社群掃描，未部署',
  NO_ENDPOINT: '無公開端點可取該代理值',
  UNVERIFIED: '端點未驗證（step0 未列出），首次嘗試失敗',
};

// ---------- 來源抓取（每函式回傳 { ok, ... }，失敗不拋錯） ----------
const OKX = 'https://www.okx.com';

async function fetchOKX(path) {
  return fetchJSON(`${OKX}${path}`);
}

// OKX 資金費率（BTC/ETH 永續，每 8h 結算，每小時可取最新值；step0 已驗 200）
async function getFunding() {
  const out = {};
  for (const sym of ['BTC', 'ETH']) {
    const r = await fetchOKX(`/api/v5/public/funding-rate?instId=${sym}-USDT-SWAP`);
    const row = r.ok && r.data && r.data.data && r.data.data[0];
    out[sym] = row ? { rate: num(row.fundingRate), time: num(row.fundingTime) } : null;
    out[sym + '_meta'] = { status: r.status, ms: r.ms, error: r.error || '' };
  }
  return out;
}

// OKX 未平倉（step0 已驗 200）
async function getOpenInterest() {
  const out = {};
  for (const sym of ['BTC', 'ETH']) {
    const r = await fetchOKX(`/api/v5/public/open-interest?instId=${sym}-USDT-SWAP`);
    const row = r.ok && r.data && r.data.data && r.data.data[0];
    out[sym] = row ? { oi: num(row.oiUsd ?? row.oi), time: num(row.ts) } : null;
    out[sym + '_meta'] = { status: r.status, ms: r.ms, error: r.error || '' };
  }
  return out;
}

// OKX K 線（1H，近 100 根；回傳時間遞增陣列）
// 注意：step0 實測 history-candles 僅回至約 4–5 天前；180 天基準須靠 KV 歷史自建。
async function getCandles(instId, limit = 100) {
  const r = await fetchOKX(`/api/v5/market/candles?instId=${instId}&bar=1H&limit=${limit}`);
  if (!r.ok || !r.data || r.data.code !== '0' || !Array.isArray(r.data.data)) {
    return { ok: false, candles: [], meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  }
  const candles = r.data.data
    .map((c) => ({ t: num(c[0]), o: num(c[1]), h: num(c[2]), l: num(c[3]), c: num(c[4]), v: num(c[5]) }))
    .filter((c) => c.t && c.c)
    .sort((a, b) => a.t - b.t);
  return { ok: true, candles, meta: { status: r.status, ms: r.ms } };
}

// OKX rubik 多空比／主動成交量（step0 未列出，未驗證；失敗即 null，不硬撐）
async function getRubik(kind, instId) {
  const path = kind === 'lsr'
    ? `/api/v5/rubik/stat/contracts/long-short-account-ratio?instId=${instId}&period=1H&limit=1`
    : `/api/v5/rubik/stat/taker-volume?instId=${instId}&period=1H&limit=1`;
  const r = await fetchOKX(path);
  if (!r.ok || !r.data || r.data.code !== '0' || !Array.isArray(r.data.data) || !r.data.data[0]) {
    return { ok: false, row: null, meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  }
  return { ok: true, row: r.data.data[0], meta: { status: r.status, ms: r.ms } };
}

// Deribit 波動率指數 DVOL（step0：Deribit 可達 200；25Δ 偏態見 getSkew）
async function getDVOL() {
  const out = {};
  for (const cur of ['BTC', 'ETH']) {
    const r = await fetchJSON(`https://www.deribit.com/api/v2/public/get_volatility_index?currency=${cur}`);
    const v = r.ok && r.data && r.data.result ? num(r.data.result.dvol ?? r.data.result.volatility_index) : null;
    out[cur] = v === null ? null : { dvol: v };
    out[cur + '_meta'] = { status: r.status, ms: r.ms, error: r.error || '' };
  }
  return out;
}

// Deribit 25Δ put−call IV 偏態（最佳努力， bounded：最近到期、現價 ±15% 內最多 3 call＋3 put）
// 失敗（無合適行權價、delta 取不到、逾時）即 null，不編數字。部署後須驗證。
async function getSkew(currency, spot) {
  try {
    const inst = await fetchJSON(`https://www.deribit.com/api/v2/public/get_instruments?currency=${currency}&kind=option&expired=false`);
    if (!inst.ok || !inst.data || !Array.isArray(inst.data.result)) return { ok: false, skew: null, meta: { status: inst.status } };
    const opts = inst.data.result.filter((o) => o.expiration_timestamp && o.strike_price);
    if (!opts.length) return { ok: false, skew: null, meta: { status: 200, error: 'no instruments' } };
    const nearestExp = Math.min(...opts.map((o) => o.expiration_timestamp));
    const near = opts.filter((o) => o.expiration_timestamp === nearestExp && Math.abs(o.strike_price - spot) / spot <= 0.15);
    const calls = near.filter((o) => o.option_type === 'call').slice(0, 3);
    const puts = near.filter((o) => o.option_type === 'put').slice(0, 3);
    if (!calls.length || !puts.length) return { ok: false, skew: null, meta: { status: 200, error: 'no near strikes' } };
    async function bestDelta(list) {
      let best = null;
      for (const o of list) {
        const t = await fetchJSON(`https://www.deribit.com/api/v2/public/ticker?instrument_name=${encodeURIComponent(o.instrument_name)}`, { timeout: 5000 });
        if (!t.ok || !t.data || !t.data.result) continue;
        const g = t.data.result.greeks || {};
        const iv = num(t.data.result.mark_iv);
        const d = num(g.delta);
        if (iv === null || d === null) continue;
        const score = Math.abs(Math.abs(d) - 0.25);
        if (!best || score < best.score) best = { iv, score };
      }
      return best ? best.iv : null;
    }
    const [callIV, putIV] = await Promise.all([bestDelta(calls), bestDelta(puts)]);
    if (callIV === null || putIV === null) return { ok: false, skew: null, meta: { status: 200, error: 'delta/iv missing' } };
    return { ok: true, skew: putIV - callIV, meta: { status: 200 } };
  } catch (e) {
    return { ok: false, skew: null, meta: { status: 0, error: String(e) } };
  }
}

// CoinGecko（免費層有速率限制：循序呼叫、間隔 300ms；429 即 null）
const CG = 'https://api.coingecko.com/api/v3';
async function getCGSimple() {
  const r = await fetchJSON(`${CG}/simple/price?ids=bitcoin,ethereum,tether-gold&vs_currencies=usd`);
  if (!r.ok || !r.data) return { ok: false, prices: {}, meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  return {
    ok: true,
    prices: { BTC: num(r.data.bitcoin?.usd), ETH: num(r.data.ethereum?.usd), XAU: num(r.data['tether-gold']?.usd) },
    meta: { status: r.status, ms: r.ms },
  };
}
async function getCGChart(id, days, interval) {
  const r = await fetchJSON(`${CG}/coins/${id}/market_chart?vs_currency=usd&days=${days}&interval=${interval}`);
  if (!r.ok || !r.data || !Array.isArray(r.data.prices)) {
    return { ok: false, prices: [], volumes: [], meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  }
  return {
    ok: true,
    prices: r.data.prices.map(([t, p]) => ({ t, p: num(p) })).filter((x) => x.p !== null),
    volumes: Array.isArray(r.data.total_volumes) ? r.data.total_volumes.map(([t, v]) => ({ t, v: num(v) })).filter((x) => x.v !== null) : [],
    meta: { status: r.status, ms: r.ms },
  };
}
async function getCGStable() {
  const r = await fetchJSON(`${CG}/coins/markets?vs_currency=usd&ids=tether,usd-coin&price_change_percentage=24h`);
  if (!r.ok || !Array.isArray(r.data)) return { ok: false, mcap: null, meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  const mcap = r.data.reduce((s, c) => s + (num(c.market_cap) || 0), 0);
  return { ok: true, mcap: mcap || null, meta: { status: r.status, ms: r.ms } };
}

// Alternative.me 恐懼貪婪指數（日更；step0 已驗 200，FGI=12）
async function getFGI() {
  const r = await fetchJSON('https://api.alternative.me/fng/?limit=1');
  const row = r.ok && r.data && Array.isArray(r.data.data) ? r.data.data[0] : null;
  if (!row) return { ok: false, value: null, time: null, meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  return { ok: true, value: num(row.value), time: num(row.timestamp) ? num(row.timestamp) * 1000 : null, meta: { status: r.status, ms: r.ms } };
}

// FRED 10 年 TIPS 殖利率 DFII10（日更；step0 從 VM 逾時，Worker 出口待驗證）
async function getFRED() {
  const r = await fetchText('https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10');
  if (!r.ok || !r.text) return { ok: false, value: null, time: null, meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  const lines = r.text.trim().split('\n').slice(1).filter(Boolean);
  const rows = lines.map((l) => {
    const [d, v] = l.split(',');
    return { date: d, value: num(v) };
  }).filter((x) => x.value !== null);
  if (!rows.length) return { ok: false, value: null, time: null, meta: { status: r.status, ms: r.ms, error: 'no rows' } };
  const last = rows[rows.length - 1];
  return { ok: true, value: last.value, time: Date.parse(last.date + 'T00:00:00Z'), history: rows.slice(-6), meta: { status: r.status, ms: r.ms } };
}

// GDELT 文章計數（新聞／敘事詞頻代理；step0 從 VM 429，Worker 出口待驗證）
// timespan=1h 的行為未驗證：若回傳計數異常龐大（>5000，疑似非 1h 區間），視為不可信 → null。
const GDELT_QUERIES = {
  crash: 'bitcoin crash OR bitcoin fear OR bitcoin ban',
  greed: 'bitcoin moon OR bitcoin ATH OR bitcoin bull',
  digitalgold: '"digital gold" bitcoin',
  safehaven: '"safe haven" gold',
  ttd: '"this time different" crypto',
  endcrypto: '"end of crypto"',
  geo: 'war OR missile OR airstrike',
  regulation: 'SEC crypto regulation OR crypto ban',
};
async function getGDELT() {
  const out = {};
  for (const [key, q] of Object.entries(GDELT_QUERIES)) {
    const url = `https://api.gdeltproject.org/api/v2/doc/doc?query=${encodeURIComponent(q)}&mode=artlist&maxrecords=1&format=json&timespan=1h`;
    const r = await fetchJSON(url);
    const n = r.ok && r.data ? num(r.data.totalArticles) : null;
    out[key] = (n !== null && n <= 5000) ? n : null;
    out[key + '_meta'] = { status: r.status, ms: r.ms, error: r.error || '', raw: n };
  }
  return out;
}

// edgeornot 強平（Grok 表引用，step0 未列出，未驗證；失敗即 null）
async function getLiquidations() {
  const r = await fetchJSON('https://edgeornot.com/api/liquidations');
  if (!r.ok || !r.data) return { ok: false, longUsd: null, shortUsd: null, meta: { status: r.status, ms: r.ms, error: r.error || '' } };
  const d = r.data.data ?? r.data;
  const longUsd = num(d.longLiquidationUsd ?? d.long_usd ?? d.long);
  const shortUsd = num(d.shortLiquidationUsd ?? d.short_usd ?? d.short);
  if (longUsd === null && shortUsd === null) return { ok: false, longUsd: null, shortUsd: null, meta: { status: r.status, ms: r.ms, error: 'schema unknown' } };
  return { ok: true, longUsd, shortUsd, meta: { status: r.status, ms: r.ms } };
}

// ---------- 打包：64 行 × 3 標的 = 192 筆（只出原始代理值 x，不算分數、不寫符號） ----------
// raw 項：{ psy, asset, x, source_time, missing_reason, window }
// source_time 為原始資料本身的時間（毫秒 epoch）；缺值時 x=null、source_time=null。
function rawItem(psy, asset, x, sourceTime, missingReason = '', window = '180d') {
  return { psy, asset, x: x === undefined ? null : x, source_time: sourceTime ?? null, missing_reason: x === null || x === undefined ? (missingReason || MR.FETCH_FAIL) : '', window };
}
function nullItem(psy, asset, reason, window = '180d') {
  return rawItem(psy, asset, null, null, reason, window);
}

function buildItems(D, S) {
  const items = [];
  const push = (psy, vals, window = '180d') => {
    for (const a of ASSETS) {
      const v = vals[a];
      if (v && typeof v === 'object' && 'x' in v) items.push(rawItem(psy, a, v.x, v.t, v.mr || '', window));
      else items.push(nullItem(psy, a, (v && v.mr) || MR.FETCH_FAIL, window));
    }
  };
  const same = (psy, x, t, mr = '', window = '180d') => {
    for (const a of ASSETS) items.push(rawItem(psy, a, x, t, mr, window));
  };
  const allNull = (psy, mr, window = '180d') => {
    for (const a of ASSETS) items.push(nullItem(psy, a, mr, window));
  };

  const nowMs = D.nowMs;
  const prevRaw = (S && S.prevRaw) || {};

  // ---- A 組：恐懼／貪婪核心 ----
  // PSY01 FGI（日更；值每日才變，source_time 為 FGI 本身時間戳）
  if (D.fgi.ok) same('PSY01', D.fgi.value, D.fgi.time, '', '1d');
  else allNull('PSY01', MR.FETCH_FAIL + '（Alternative.me）', '1d');

  // PSY02 資金費率（OKX；XAU 無資金費率）
  push('PSY02', {
    BTC: D.funding.BTC ? { x: D.funding.BTC.rate, t: D.funding.BTC.time } : { mr: MR.FETCH_FAIL + '（OKX funding-rate）' },
    ETH: D.funding.ETH ? { x: D.funding.ETH.rate, t: D.funding.ETH.time } : { mr: MR.FETCH_FAIL + '（OKX funding-rate）' },
    XAU: { mr: '黃金無永續資金費率' },
  });
  // PSY03／04 強平（edgeornot 首次嘗試；Coinglass 無 key 回 500）
  const liqT = nowMs;
  if (D.liq.ok) {
    push('PSY03', { BTC: { x: D.liq.longUsd, t: liqT }, ETH: { x: D.liq.longUsd, t: liqT }, XAU: { mr: '強平為加密合約指標，不適用黃金' } });
    push('PSY04', { BTC: { x: D.liq.shortUsd, t: liqT }, ETH: { x: D.liq.shortUsd, t: liqT }, XAU: { mr: '強平為加密合約指標，不適用黃金' } });
  } else {
    allNull('PSY03', '強平來源須 API key（Coinglass 無 key 回 500）；edgeornot 首次嘗試失敗');
    allNull('PSY04', '強平來源須 API key（Coinglass 無 key 回 500）；edgeornot 首次嘗試失敗');
  }
  // PSY05 OI 1h 變化率（需上一小時原始 OI；暖機第一包為 null）
  const oiChange = (sym) => {
    const cur = D.oi[sym];
    const prev = prevRaw.oi && prevRaw.oi[sym];
    if (!cur || prev === undefined || prev === null || prev === 0) return { mr: cur ? MR.WARMUP : MR.FETCH_FAIL + '（OKX open-interest）' };
    return { x: (cur.oi - prev) / prev, t: cur.time };
  };
  push('PSY05', { BTC: oiChange('BTC'), ETH: oiChange('ETH'), XAU: { mr: '黃金無永續 OI' } });
  // PSY06 多空比（OKX rubik，未驗證端點；失敗即 null）
  push('PSY06', {
    BTC: D.lsr.BTC ? { x: D.lsr.BTC, t: nowMs } : { mr: 'OKX long-short ratio 端點未驗證／抓取失敗；Binance 多空比地理封鎖（451）' },
    ETH: D.lsr.ETH ? { x: D.lsr.ETH, t: nowMs } : { mr: 'OKX long-short ratio 端點未驗證／抓取失敗；Binance 多空比地理封鎖（451）' },
    XAU: { mr: '黃金無多空比' },
  });
  // PSY07 1h 實現波動率（OKX K 線自算；XAU 用 CoinGecko 小時價）
  const rv = (closes) => {
    const rets = logReturns(closes);
    if (rets.length < 12) return null;
    return stdev(rets.slice(-24));
  };
  const rvT = (D.candles.BTC.ok && D.candles.BTC.candles.length) ? D.candles.BTC.candles[D.candles.BTC.candles.length - 1].t : null;
  push('PSY07', {
    BTC: D.candles.BTC.ok ? { x: rv(D.candles.BTC.candles.map((c) => c.c)), t: rvT } : { mr: MR.FETCH_FAIL + '（OKX candles）' },
    ETH: D.candles.ETH.ok ? { x: rv(D.candles.ETH.candles.map((c) => c.c)), t: rvT } : { mr: MR.FETCH_FAIL + '（OKX candles）' },
    XAU: D.hourly.XAU.ok ? { x: rv(D.hourly.XAU.prices.map((p) => p.p)), t: nowMs } : { mr: MR.FETCH_FAIL + '（CoinGecko XAUt 小時價）' },
  });
  // PSY08 期權 25Δ 偏態（Deribit 期權鏈遍歷，最佳努力；XAU 無）
  push('PSY08', {
    BTC: D.skew.BTC && D.skew.BTC.ok ? { x: D.skew.BTC.skew, t: nowMs } : { mr: 'Deribit 25Δ 偏態未取到（行權價／delta 缺失或逾時）' },
    ETH: D.skew.ETH && D.skew.ETH.ok ? { x: D.skew.ETH.skew, t: nowMs } : { mr: 'Deribit 25Δ 偏態未取到（行權價／delta 缺失或逾時）' },
    XAU: { mr: '黃金無 Deribit 期權偏態' },
  });

  // ---- B 組：從眾／社群／注意力 ----
  allNull('PSY09', MR.PAYWALL + '（LunarCrush／The Tie 社群情緒極性）');
  allNull('PSY10', MR.PAYWALL + '（LunarCrush／The Tie 社群提及量）');
  // PSY11／12 新聞詞頻（GDELT 文章計數代理；timespan=1h 行為未驗證，見 sources-mapping）
  if (D.gdelt.crash !== null && D.gdelt.crash !== undefined) same('PSY11', D.gdelt.crash, nowMs);
  else allNull('PSY11', 'GDELT 抓取失敗／計數不可信（step0 從 VM 429）');
  if (D.gdelt.greed !== null && D.gdelt.greed !== undefined) same('PSY12', D.gdelt.greed, nowMs);
  else allNull('PSY12', 'GDELT 抓取失敗／計數不可信（step0 從 VM 429）');
  allNull('PSY13', '無穩定每小時公開 API（Google Trends 429）', '1d');
  allNull('PSY14', '無穩定每小時公開 API（Google Trends 429）', '1d');
  // PSY15／16 敘事提及（GDELT 文章計數代理）
  if (D.gdelt.digitalgold !== null && D.gdelt.digitalgold !== undefined) same('PSY15', D.gdelt.digitalgold, nowMs);
  else allNull('PSY15', 'GDELT 抓取失敗／計數不可信');
  if (D.gdelt.safehaven !== null && D.gdelt.safehaven !== undefined) same('PSY16', D.gdelt.safehaven, nowMs);
  else allNull('PSY16', 'GDELT 抓取失敗／計數不可信');

  // ---- C 組：錨定／參考點（價格自算） ----
  const closesOf = (sym) => (D.candles[sym] && D.candles[sym].ok ? D.candles[sym].candles.map((c) => c.c) : null);
  const highs24 = (sym) => (D.candles[sym] && D.candles[sym].ok ? D.candles[sym].candles.slice(-24).map((c) => c.h) : null);
  const lows24 = (sym) => (D.candles[sym] && D.candles[sym].ok ? D.candles[sym].candles.slice(-24).map((c) => c.l) : null);
  const xauHourly = D.hourly.XAU.ok ? D.hourly.XAU.prices.map((p) => p.p) : null;
  const lastPrice = (sym) => {
    if (sym === 'XAU') return xauHourly && xauHourly.length ? xauHourly[xauHourly.length - 1] : null;
    const c = closesOf(sym);
    return c && c.length ? c[c.length - 1] : null;
  };
  const anchorRow = (psy, fn) => {
    push(psy, {
      BTC: (() => { const v = fn('BTC', closesOf('BTC'), highs24('BTC'), lows24('BTC')); return v === null ? { mr: MR.FETCH_FAIL + '（OKX candles）' } : { x: v, t: rvT }; })(),
      ETH: (() => { const v = fn('ETH', closesOf('ETH'), highs24('ETH'), lows24('ETH')); return v === null ? { mr: MR.FETCH_FAIL + '（OKX candles）' } : { x: v, t: rvT }; })(),
      XAU: (() => { const v = fn('XAU', xauHourly, xauHourly ? xauHourly.slice(-24) : null, xauHourly ? xauHourly.slice(-24) : null); return v === null ? { mr: MR.FETCH_FAIL + '（CoinGecko XAUt 小時價）' } : { x: v, t: nowMs }; })(),
    });
  };
  // PSY17 前高錨定距離 (P−H24)/H24；PSY18 前低 (P−L24)/L24
  anchorRow('PSY17', (sym, closes, highs) => {
    if (!closes || !closes.length || !highs || !highs.length) return null;
    const p = closes[closes.length - 1], h = Math.max(...highs);
    return h ? (p - h) / h : null;
  });
  anchorRow('PSY18', (sym, closes, highs, lows) => {
    if (!closes || !closes.length || !lows || !lows.length) return null;
    const p = closes[closes.length - 1], l = Math.min(...lows);
    return l ? (p - l) / l : null;
  });
  // PSY19 整數關卡錨定：|P−錨點|／錨點（BTC 每 1000、ETH 每 100、XAU 每 50）
  anchorRow('PSY19', (sym) => {
    const p = lastPrice(sym);
    if (p === null) return null;
    const step = sym === 'BTC' ? 1000 : sym === 'ETH' ? 100 : 50;
    const anchor = Math.round(p / step) * step;
    return anchor ? Math.abs(p - anchor) / anchor : null;
  });
  // PSY20 52 週高點錨定 ln(P／H365)；PSY21 均值回歸偏離 Z；PSY23 技術突破（+1／0／−1）
  const dailyCloses = (sym) => {
    const key = sym === 'XAU' ? 'XAU' : sym;
    const d = D.daily[key];
    return d && d.ok ? d.prices.map((p) => p.p) : null;
  };
  const breakout = (sym) => {
    const dc = dailyCloses(sym);
    if (!dc || dc.length < 31) return null;
    const p = dc[dc.length - 1];
    const h30 = Math.max(...dc.slice(-31, -1));
    const l30 = Math.min(...dc.slice(-31, -1));
    if (p > h30) return 1;
    if (p < l30) return -1;
    return 0;
  };
  push('PSY20', {
    BTC: (() => { const dc = dailyCloses('BTC'); if (!dc || !dc.length) return { mr: MR.FETCH_FAIL + '（CoinGecko 365d）' }; const h = Math.max(...dc); return { x: Math.log(dc[dc.length - 1] / h), t: nowMs }; })(),
    ETH: (() => { const dc = dailyCloses('ETH'); if (!dc || !dc.length) return { mr: MR.FETCH_FAIL + '（CoinGecko 365d）' }; const h = Math.max(...dc); return { x: Math.log(dc[dc.length - 1] / h), t: nowMs }; })(),
    XAU: (() => { const dc = dailyCloses('XAU'); if (!dc || !dc.length) return { mr: MR.FETCH_FAIL + '（CoinGecko 365d）' }; const h = Math.max(...dc); return { x: Math.log(dc[dc.length - 1] / h), t: nowMs }; })(),
  });
  push('PSY21', {
    BTC: (() => { const dc = dailyCloses('BTC'); if (!dc || dc.length < 31) return { mr: MR.FETCH_FAIL + '（CoinGecko 365d，日數不足）' }; const ma = mean(dc.slice(-31, -1)); const sd = stdev(dc.slice(-31, -1)); return (ma === null || !sd) ? { mr: MR.FETCH_FAIL } : { x: (dc[dc.length - 1] - ma) / sd, t: nowMs }; })(),
    ETH: (() => { const dc = dailyCloses('ETH'); if (!dc || dc.length < 31) return { mr: MR.FETCH_FAIL + '（CoinGecko 365d，日數不足）' }; const ma = mean(dc.slice(-31, -1)); const sd = stdev(dc.slice(-31, -1)); return (ma === null || !sd) ? { mr: MR.FETCH_FAIL } : { x: (dc[dc.length - 1] - ma) / sd, t: nowMs }; })(),
    XAU: (() => { const dc = dailyCloses('XAU'); if (!dc || dc.length < 31) return { mr: MR.FETCH_FAIL + '（CoinGecko 365d，日數不足）' }; const ma = mean(dc.slice(-31, -1)); const sd = stdev(dc.slice(-31, -1)); return (ma === null || !sd) ? { mr: MR.FETCH_FAIL } : { x: (dc[dc.length - 1] - ma) / sd, t: nowMs }; })(),
  });
  // PSY22 動量自相關 ρ（24 根 1h 報酬）；PSY24 近因 r1／r24
  const hourlyPrices = (sym) => (sym === 'XAU' ? xauHourly : closesOf(sym));
  const rhoRow = (psy) => {
    push(psy, {
      BTC: (() => { const rets = logReturns(hourlyPrices('BTC') || []); const v = autocorr1(rets.slice(-24)); return v === null ? { mr: MR.FETCH_FAIL + '（小時報酬不足 24 根）' } : { x: v, t: rvT }; })(),
      ETH: (() => { const rets = logReturns(hourlyPrices('ETH') || []); const v = autocorr1(rets.slice(-24)); return v === null ? { mr: MR.FETCH_FAIL + '（小時報酬不足 24 根）' } : { x: v, t: rvT }; })(),
      XAU: (() => { const rets = logReturns(hourlyPrices('XAU') || []); const v = autocorr1(rets.slice(-24)); return v === null ? { mr: MR.FETCH_FAIL + '（小時報酬不足 24 根）' } : { x: v, t: nowMs }; })(),
    });
  };
  rhoRow('PSY22');
  push('PSY23', {
    BTC: (() => { const v = breakout('BTC'); return v === null ? { mr: MR.FETCH_FAIL + '（CoinGecko 365d，日數不足）' } : { x: v, t: nowMs }; })(),
    ETH: (() => { const v = breakout('ETH'); return v === null ? { mr: MR.FETCH_FAIL + '（CoinGecko 365d，日數不足）' } : { x: v, t: nowMs }; })(),
    XAU: (() => { const v = breakout('XAU'); return v === null ? { mr: MR.FETCH_FAIL + '（CoinGecko 365d，日數不足）' } : { x: v, t: nowMs }; })(),
  });
  push('PSY24', {
    BTC: (() => { const p = closesOf('BTC'); if (!p || p.length < 25) return { mr: MR.FETCH_FAIL + '（小時價不足 25 根）' }; const r1 = p[p.length - 1] / p[p.length - 2] - 1; const r24 = p[p.length - 1] / p[p.length - 25] - 1; return r24 === 0 ? { mr: MR.FETCH_FAIL } : { x: Math.sign(r1) * Math.min(Math.abs(r1) / Math.abs(r24), 1), t: rvT }; })(),
    ETH: (() => { const p = closesOf('ETH'); if (!p || p.length < 25) return { mr: MR.FETCH_FAIL + '（小時價不足 25 根）' }; const r1 = p[p.length - 1] / p[p.length - 2] - 1; const r24 = p[p.length - 1] / p[p.length - 25] - 1; return r24 === 0 ? { mr: MR.FETCH_FAIL } : { x: Math.sign(r1) * Math.min(Math.abs(r1) / Math.abs(r24), 1), t: rvT }; })(),
    XAU: (() => { const p = xauHourly; if (!p || p.length < 25) return { mr: MR.FETCH_FAIL + '（小時價不足 25 根）' }; const r1 = p[p.length - 1] / p[p.length - 2] - 1; const r24 = p[p.length - 1] / p[p.length - 25] - 1; return r24 === 0 ? { mr: MR.FETCH_FAIL } : { x: Math.sign(r1) * Math.min(Math.abs(r1) / Math.abs(r24), 1), t: nowMs }; })(),
  });

  // ---- D 組：損失／處置／持有 ----
  allNull('PSY25', MR.PAYWALL + '（Glassnode SOPR，1h 須付費）');
  allNull('PSY26', MR.PAYWALL + '（鏈上已實現損益）');
  allNull('PSY27', MR.PAYWALL + '（鏈上已實現損益）');
  allNull('PSY28', '無每小時來源（Glassnode HODL waves 日更為主，須付費）', '1d');
  allNull('PSY29', '無每小時來源（Glassnode MVRV 日更為主，須付費）', '1d');
  allNull('PSY30', 'CryptoQuant 403，交易所淨流入免費層未驗證可用');
  allNull('PSY31', 'CryptoQuant 403，交易所淨流出免費層未驗證可用');
  allNull('PSY32', MR.PAYWALL + '（Whale Alert 鯨魚轉帳須 API key）');

  // ---- E 組：偏誤組合 ----
  // PSY33 過度自信槓桿：資金費率絕對值（極端值代理）
  push('PSY33', {
    BTC: D.funding.BTC ? { x: Math.abs(D.funding.BTC.rate), t: D.funding.BTC.time } : { mr: MR.FETCH_FAIL + '（OKX funding-rate）' },
    ETH: D.funding.ETH ? { x: Math.abs(D.funding.ETH.rate), t: D.funding.ETH.time } : { mr: MR.FETCH_FAIL + '（OKX funding-rate）' },
    XAU: { mr: '黃金無資金費率' },
  });
  // PSY34／35／36 主動買賣比（OKX rubik taker-volume，未驗證端點）
  const takerX = (sym, side) => {
    const tk = D.taker[sym];
    if (!tk) return { mr: 'OKX taker-volume 端點未驗證／抓取失敗；Binance aggTrades 地理封鎖（451）' };
    const tot = tk.buy + tk.sell;
    if (!tot) return { mr: MR.FETCH_FAIL + '（taker-volume 總量為 0）' };
    return { x: side === 'buy' ? tk.buy / tot : tk.sell / tot, t: nowMs };
  };
  push('PSY34', { BTC: takerX('BTC', 'buy'), ETH: takerX('ETH', 'buy'), XAU: { mr: '黃金無 taker buy-sell' } });
  push('PSY35', { BTC: takerX('BTC', 'sell'), ETH: takerX('ETH', 'sell'), XAU: { mr: '黃金無 taker buy-sell' } });
  push('PSY36', { BTC: takerX('BTC', 'sell'), ETH: takerX('ETH', 'sell'), XAU: { mr: '黃金無 taker buy-sell' } });
  // PSY37 合約量占比（OKX 合約 K 線量／（現貨＋合約）量）
  const contractShare = (sym) => {
    const sw = D.candles[sym] && D.candles[sym].ok ? D.candles[sym].candles : null;
    const sp = D.spotCandles[sym] && D.spotCandles[sym].ok ? D.spotCandles[sym].candles : null;
    if (!sw || !sw.length || !sp || !sp.length) return { mr: MR.FETCH_FAIL + '（OKX 合約／現貨 K 線）' };
    const sv = sw[sw.length - 1].v, pv = sp[sp.length - 1].v;
    if (sv === null || pv === null || sv + pv === 0) return { mr: MR.FETCH_FAIL + '（成交量為空）' };
    return { x: sv / (sv + pv), t: rvT };
  };
  push('PSY37', { BTC: contractShare('BTC'), ETH: contractShare('ETH'), XAU: { mr: '黃金無合約／現貨量比' } });
  allNull('PSY38', MR.NO_ENDPOINT + '（OI 期限結構無公開端點）');
  // PSY39 刪除候選（對照組）：仍出現在 bundle，x 為 null（需 NLP，未部署）
  allNull('PSY39', MR.NLP_MISSING + '（後見之明詞頻；刪除候選，對照組，定義保留）');
  // PSY40 刪除候選（對照組）：成交量異常低可自算，x 照常出（計分與否由下游檢定決定）
  const lowVol = (sym) => {
    const cs = D.candles[sym] && D.candles[sym].ok ? D.candles[sym].candles : null;
    if (!cs || cs.length < 25) return null;
    const vols = cs.slice(-25, -1).map((c) => c.v);
    const med = median(vols);
    const last = cs[cs.length - 1].v;
    return med ? last / med : null;
  };
  push('PSY40', {
    BTC: (() => { const v = lowVol('BTC'); return v === null ? { mr: MR.FETCH_FAIL + '（OKX candles 量不足）' } : { x: v, t: rvT }; })(),
    ETH: (() => { const v = lowVol('ETH'); return v === null ? { mr: MR.FETCH_FAIL + '（OKX candles 量不足）' } : { x: v, t: rvT }; })(),
    XAU: (() => { const vs = D.hourly.XAU.ok ? D.hourly.XAU.volumes : []; if (vs.length < 25) return { mr: MR.FETCH_FAIL + '（CoinGecko 量不足）' }; const med = median(vs.slice(-25, -1).map((x) => x.v)); const last = vs[vs.length - 1].v; return med ? { x: last / med, t: nowMs } : { mr: MR.FETCH_FAIL }; })(),
  });

  // ---- F 組：敘事／信念／框架 ----
  allNull('PSY41', MR.PAYWALL + '（社群同溫層，須社群 API）');
  if (D.gdelt.ttd !== null && D.gdelt.ttd !== undefined) same('PSY42', D.gdelt.ttd, nowMs);
  else allNull('PSY42', 'GDELT 抓取失敗／計數不可信');
  if (D.gdelt.endcrypto !== null && D.gdelt.endcrypto !== undefined) same('PSY43', D.gdelt.endcrypto, nowMs);
  else allNull('PSY43', 'GDELT 抓取失敗／計數不可信');
  allNull('PSY44', 'X 追蹤列表須人工／API，無自動事件源');
  allNull('PSY45', MR.EVENT_ONLY + '（機構研報發布，無自動事件源）');
  allNull('PSY46', MR.NLP_MISSING + '（稀缺敘事提及頻率）');
  allNull('PSY47', MR.PAYWALL + '（空投提及，社群來源付費牆）');
  allNull('PSY48', '人工／半自動追蹤，無自動源（刪除候選，定義保留）');

  // ---- G 組：社會／賭性／反應 ----
  allNull('PSY49', '複合指標需社群提及同步，社群來源付費牆');
  allNull('PSY50', '複合指標需強平數據，強平來源須 API key');
  allNull('PSY51', '需新聞時間戳＋價格振幅，新聞源未部署');
  allNull('PSY52', MR.PAYWALL + '（鏈上活躍地址須付費）');
  // PSY53 賭徒謬誤／PSY54 熱手謬誤：同一連漲小時數（下游符號相反，不在此預設）
  const streakRow = (psy) => {
    push(psy, {
      BTC: (() => { const p = closesOf('BTC'); if (!p || p.length < 2) return { mr: MR.FETCH_FAIL }; return { x: upStreak(p), t: rvT }; })(),
      ETH: (() => { const p = closesOf('ETH'); if (!p || p.length < 2) return { mr: MR.FETCH_FAIL }; return { x: upStreak(p), t: rvT }; })(),
      XAU: (() => { const p = xauHourly; if (!p || p.length < 2) return { mr: MR.FETCH_FAIL }; return { x: upStreak(p), t: nowMs }; })(),
    });
  };
  streakRow('PSY53');
  streakRow('PSY54');
  allNull('PSY55', MR.PAYWALL + '（極端情緒帖占比，須社群 API）');
  allNull('PSY56', MR.PAYWALL + '（鏈上跟單，須鏈上數據）');

  // ---- H 組：宏觀／避險／跨資產 ----
  // PSY57 黃金 1h 漲跌幅（同一 r金，三標的皆出；下游符號處理）
  const goldRet = (() => {
    const p = xauHourly;
    if (!p || p.length < 2) return null;
    return p[p.length - 1] / p[p.length - 2] - 1;
  })();
  if (goldRet !== null) same('PSY57', goldRet, nowMs);
  else allNull('PSY57', MR.FETCH_FAIL + '（CoinGecko XAUt 小時價）');
  allNull('PSY58', MR.NO_ENDPOINT + '（DXY 無公開每小時端點，TradingView 無公開 API）');
  // PSY59 實質利率：FRED DFII10 5 日變動（需歷史，state 累積；暖機 null）
  if (D.fred.ok) {
    const hist = [...(prevRaw.fredHistory || []), D.fred.value].filter((v) => v !== null && v !== undefined);
    if (hist.length >= 6) same('PSY59', D.fred.value - hist[hist.length - 6], D.fred.time, '', '1d');
    else allNull('PSY59', MR.WARMUP + '（FRED 5 日變動需 6 筆日值累積）', '1d');
  } else {
    allNull('PSY59', 'FRED 從本 VM 逾時，Worker 出口待驗證', '1d');
  }
  if (D.gdelt.geo !== null && D.gdelt.geo !== undefined) same('PSY60', D.gdelt.geo, nowMs);
  else allNull('PSY60', 'GDELT 抓取失敗／計數不可信');
  allNull('PSY61', '多無每小時來源（黃金 ETF 流入／GVZ 部分日更）', '1d');
  if (D.gdelt.regulation !== null && D.gdelt.regulation !== undefined) same('PSY62', D.gdelt.regulation, nowMs);
  else allNull('PSY62', 'GDELT 抓取失敗／計數不可信');
  allNull('PSY63', '日更為主，須付費（CryptoQuant／Nansen）', '1d');
  // PSY64 穩定幣市值（USDT＋USDC 合計，日更節奏）
  if (D.stable.ok && D.stable.mcap !== null) same('PSY64', D.stable.mcap, nowMs, '', '1d');
  else allNull('PSY64', MR.FETCH_FAIL + '（CoinGecko 穩定幣市值）', '1d');

  return items;
}

// ---------- 完成 bundle：補 last_valid_time／stale_age_h／is_duplicate ----------
function finalize(items, bundleTimeMs, prevItems) {
  const out = [];
  for (const it of items) {
    const key = `${it.psy}_${it.asset}`;
    const prev = prevItems ? prevItems[key] : null;
    let lastValid = null;
    if (it.x !== null && it.x !== undefined) lastValid = it.source_time;
    else if (prev && prev.last_valid_time) lastValid = prev.last_valid_time;
    const stale = lastValid === null ? null : Math.max(0, Math.floor((bundleTimeMs - lastValid) / 3600000));
    const dup = it.x !== null && it.x !== undefined && prev && prev.x !== null && prev.x !== undefined && prev.x === it.x;
    out.push({
      psy: it.psy, asset: it.asset, x: it.x, source_time: it.source_time ? taipeiISO(new Date(it.source_time)) : null,
      fetch_time: taipeiISO(new Date(bundleTimeMs)), last_valid_time: lastValid ? taipeiISO(new Date(lastValid)) : null,
      window: it.window, stale_age_h: stale, is_duplicate: !!dup, missing_reason: it.missing_reason || '',
    });
  }
  return out;
}

// ---------- 每小時執行 ----------
async function runHourly(env, scheduledTimeMs) {
  const bundleDate = new Date(scheduledTimeMs);
  const bundleTimeMs = hourFloor(bundleDate).getTime();
  const bundleTime = taipeiISO(new Date(bundleTimeMs));
  const hourKey = taipeiHourKey(new Date(bundleTimeMs));
  const meta = { sources: {}, errors: [] };

  // 讀上一包 state（變化率、last_valid_time、is_duplicate 用）
  let state = { prevRaw: {}, prevItems: {} };
  try {
    const s = await env.PSY64_KV.get('state.json', 'json');
    if (s) state = s;
  } catch (e) { meta.errors.push('state read failed: ' + String(e)); }

  // 並行拉取（CoinGecko 免費層限速：組內循序、間隔 300ms）
  const [funding, oi, candlesBTC, candlesETH, spotBTC, spotETH, dvol, fgi, fred, gdelt, liq] = await Promise.all([
    getFunding(),
    getOpenInterest(),
    getCandles('BTC-USDT-SWAP'),
    getCandles('ETH-USDT-SWAP'),
    getCandles('BTC-USDT'),
    getCandles('ETH-USDT'),
    getDVOL(),
    getFGI(),
    getFRED(),
    getGDELT(),
    getLiquidations(),
  ]);
  const [lsrBTC, lsrETH, takerBTC, takerETH] = await Promise.all([
    getRubik('lsr', 'BTC-USDT-SWAP'),
    getRubik('lsr', 'ETH-USDT-SWAP'),
    getRubik('taker', 'BTC-USDT-SWAP'),
    getRubik('taker', 'ETH-USDT-SWAP'),
  ]);
  const cgSimple = await getCGSimple();
  await sleep(300);
  const cgHourlyBTC = await getCGChart('bitcoin', 2, 'hourly');
  await sleep(300);
  const cgHourlyETH = await getCGChart('ethereum', 2, 'hourly');
  await sleep(300);
  const cgHourlyXAU = await getCGChart('tether-gold', 2, 'hourly');
  await sleep(300);
  const cgDailyBTC = await getCGChart('bitcoin', 365, 'daily');
  await sleep(300);
  const cgDailyETH = await getCGChart('ethereum', 365, 'daily');
  await sleep(300);
  const cgDailyXAU = await getCGChart('tether-gold', 365, 'daily');
  await sleep(300);
  const cgStable = await getCGStable();

  // Deribit 偏態（需現價定行權價範圍；用 OKX 收盤價）
  const priceBTC = candlesBTC.ok && candlesBTC.candles.length ? candlesBTC.candles[candlesBTC.candles.length - 1].c : (cgSimple.ok ? cgSimple.prices.BTC : null);
  const priceETH = candlesETH.ok && candlesETH.candles.length ? candlesETH.candles[candlesETH.candles.length - 1].c : (cgSimple.ok ? cgSimple.prices.ETH : null);
  const [skewBTC, skewETH] = await Promise.all([
    priceBTC ? getSkew('BTC', priceBTC) : { ok: false, skew: null, meta: { error: 'no spot' } },
    priceETH ? getSkew('ETH', priceETH) : { ok: false, skew: null, meta: { error: 'no spot' } },
  ]);

  const D = {
    nowMs: bundleTimeMs,
    funding, oi,
    candles: { BTC: candlesBTC, ETH: candlesETH },
    spotCandles: { BTC: spotBTC, ETH: spotETH },
    lsr: {
      BTC: lsrBTC.ok ? num(lsrBTC.row.longShortRatio) : null,
      ETH: lsrETH.ok ? num(lsrETH.row.longShortRatio) : null,
    },
    taker: {
      BTC: takerBTC.ok ? { buy: num(takerBTC.row.buyVol), sell: num(takerBTC.row.sellVol) } : null,
      ETH: takerETH.ok ? { buy: num(takerETH.row.buyVol), sell: num(takerETH.row.sellVol) } : null,
    },
    dvol,
    skew: { BTC: skewBTC, ETH: skewETH },
    cgSimple,
    hourly: { BTC: cgHourlyBTC, ETH: cgHourlyETH, XAU: cgHourlyXAU },
    daily: { BTC: cgDailyBTC, ETH: cgDailyETH, XAU: cgDailyXAU },
    stable: cgStable,
    fgi, fred, gdelt, liq,
  };

  // 來源狀態彙總（寫進 bundle.meta，不影響分數）
  const srcStatus = (name, ok, m) => { meta.sources[name] = { ok: !!ok, status: m ? m.status : null, ms: m ? m.ms : null, error: m ? (m.error || '') : '' }; };
  srcStatus('okx_funding', funding.BTC || funding.ETH, funding.BTC_meta);
  srcStatus('okx_oi', oi.BTC || oi.ETH, oi.BTC_meta);
  srcStatus('okx_candles', candlesBTC.ok, candlesBTC.meta);
  srcStatus('okx_rubik_lsr', lsrBTC.ok, lsrBTC.meta);
  srcStatus('okx_rubik_taker', takerBTC.ok, takerBTC.meta);
  srcStatus('deribit_dvol', dvol.BTC, dvol.BTC_meta);
  srcStatus('deribit_skew_btc', skewBTC.ok, skewBTC.meta);
  srcStatus('coingecko_simple', cgSimple.ok, cgSimple.meta);
  srcStatus('coingecko_hourly', cgHourlyBTC.ok, cgHourlyBTC.meta);
  srcStatus('coingecko_daily365', cgDailyBTC.ok, cgDailyBTC.meta);
  srcStatus('coingecko_stable', cgStable.ok, cgStable.meta);
  srcStatus('alternative_fgi', fgi.ok, fgi.meta);
  srcStatus('fred_dfii10', fred.ok, fred.meta);
  srcStatus('gdelt', gdelt.crash !== null && gdelt.crash !== undefined, gdelt.crash_meta);
  srcStatus('edgeornot_liq', liq.ok, liq.meta);

  const rawItems = buildItems(D, state);
  const items = finalize(rawItems, bundleTimeMs, state.prevItems);

  const bundle = { bundle_time: bundleTime, items, meta };
  const bundleText = JSON.stringify(bundle);

  // 存 KV：最新一包＋歷史 append-only（45 天 TTL）＋state
  await env.PSY64_KV.put('bundle.json', bundleText);
  await env.PSY64_KV.put(`bundle-history/${hourKey}`, bundleText, { expirationTtl: HISTORY_TTL_S });
  const prevItems = {};
  for (const it of items) prevItems[`${it.psy}_${it.asset}`] = { x: it.x, last_valid_time: it.last_valid_time ? Date.parse(it.last_valid_time) : null };
  const fredHistory = fred.ok
    ? [...(state.prevRaw.fredHistory || []), fred.value].filter((v) => v !== null && v !== undefined).slice(-10)
    : (state.prevRaw.fredHistory || []);
  const newState = {
    prevRaw: {
      oi: {
        BTC: oi.BTC ? oi.BTC.oi : (state.prevRaw.oi ? state.prevRaw.oi.BTC : null),
        ETH: oi.ETH ? oi.ETH.oi : (state.prevRaw.oi ? state.prevRaw.oi.ETH : null),
      },
      fredHistory,
    },
    prevItems,
    updated_at: bundleTime,
  };
  await env.PSY64_KV.put('state.json', JSON.stringify(newState));

  const nNull = items.filter((i) => i.x === null).length;
  console.log(`[psy64] bundle ${bundleTime} items=${items.length} null=${nNull} bytes=${bundleText.length}`);
  return { bundleTime, items: items.length, nulls: nNull };
}

// ---------- HTTP 入口 ----------
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runHourly(env, event.scheduledTime).catch((e) => console.error('[psy64] scheduled failed', e)));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === '/bundle.json' || url.pathname === '/') {
      const text = await env.PSY64_KV.get('bundle.json');
      if (!text) return new Response(JSON.stringify({ error: 'no bundle yet (cron not run)' }), { status: 503, headers: { 'content-type': 'application/json' } });
      return new Response(text, { headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } });
    }
    if (url.pathname === '/health') {
      const text = await env.PSY64_KV.get('bundle.json');
      let bundleTime = null, items = 0, nulls = 0;
      if (text) {
        try {
          const b = JSON.parse(text);
          bundleTime = b.bundle_time;
          items = (b.items || []).length;
          nulls = (b.items || []).filter((i) => i.x === null).length;
        } catch { /* ignore */ }
      }
      return new Response(JSON.stringify({ ok: true, bundle_time: bundleTime, items, nulls }), { headers: { 'content-type': 'application/json' } });
    }
    return new Response(JSON.stringify({ error: 'not found' }), { status: 404, headers: { 'content-type': 'application/json' } });
  },
};
