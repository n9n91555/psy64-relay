# psy64-relay — 心理64 Cloudflare Worker 中繼

心理 64 面向每小時抓取整包的中繼。Worker 只出**原始代理值 x**，不算情緒分數、
不寫死符號或因果（主人 11:24 立規第 3 條）；分數由 Apps Script `Psy64.gs`
按定稿 §6.0 統一式 `S=100·tanh(z/2)` 算。

- 定稿：`~/workspace/consult-trial/consult-20261127-psych64/verdict.md` §6、§8、§9
- 契約：`~/workspace/apps-script/factor-fleet/Psy64.gs` 開頭註解
- 實測：`~/workspace/consult-trial/consult-20261127-psych64/step0-endpoint-check.md`
- 來源對照：`sources-mapping.md`

## 架構

```
每小時整點（cron 0 * * * *)
  Worker scheduled → 平行拉取 OKX / Deribit / CoinGecko / Alternative.me /
                     FRED / GDELT / edgeornot（單一來源 8 秒逾時，失敗不拖垮整包）
  → 打包 192 筆（PSY01–PSY64 × BTC／ETH／XAU）
  → KV: bundle.json（最新一包）＋ bundle-history/<台北小時>（append-only，45 天 TTL）
        ＋ state.json（上一小時原始值，供變化率／last_valid_time 用）
  → Sheets Psy64.gs 每小時 GET /bundle.json 一次，寫入 Psych64_每小時
```

缺值一律寫 `null` 並填 `missing_reason`，絕不以 0 代替。
`stale_age_h` 為資料年齡（小時）；無新值時沿用 `last_valid_time` 計算。
`is_duplicate` 為本包原始值與上一包相同。

## 部署步驟（帳號接通後）

```bash
cd ~/workspace/psy64-relay
npm install -g wrangler        # 若尚未安裝
wrangler login                 # 瀏覽器登入 Cloudflare（主人親手登入）
wrangler kv namespace create psy64-kv
# 把回傳的 id 填進 wrangler.toml 的 kv_namespaces.id（取代 REPLACE_WITH_KV_NAMESPACE_ID）
wrangler deploy
```

部署後驗證（約等下一個整點，或手動觸發一次）：

```bash
curl -s https://psy64-relay.<你的 workers.dev 子網域>/health
# 應回 {"ok":true,"bundle_time":"...","items":192,"nulls":<數字>}

curl -s https://psy64-relay.<你的 workers.dev 子網域>/bundle.json | head -c 500
# 應看到 bundle_time 與 192 筆 items；缺值筆 missing_reason 非空
```

驗證通過後，把 URL 填進 `Psy64.gs` 的 `PSY_RELAY_URL`，再把 `Psy64.gs`
貼進試算表綁定專案、授權、執行 `installPsy64Triggers`。

## 每小時排程說明

- cron `0 * * * *`：每小時整點觸發一次（UTC 整點＝台北整點）。
- 每次執行約 40 多次子請求（Workers 免費版每請求上限 50 次子請求，目前
  約 44 次，餘量很小；見下方「已知限制」）。
- 單一來源逾時 8 秒；CoinGecko 免費層限速，組內循序呼叫、間隔 300ms。
- 暖機：PSY05（OI 變化率）與 PSY59（FRED 5 日變動）需上一小時／前 5 日
  原始值累積，第一包為 null，屬正常；180 天正規化基準由 KV 歷史
  append-only 自建，暖機期分數顯示 NA（定稿 §6.0）。

## 如何看 log

```bash
wrangler tail   # 即時 log；每包會印一行：
# [psy64] bundle 2026-11-27T11:00:00+08:00 items=192 null=98 bytes=45392
```

`null` 數偏高屬正常：付費牆／無每小時來源的面向目前皆為 null
（見 `sources-mapping.md`）。`null` 數突然暴增或 `items` 不是 192，
先看 `bundle.json` 的 `meta.sources`（每來源 HTTP 狀態與耗時）。

## 如何換來源

1. 在 `src/index.js` 的「來源抓取」區新增／修改 fetcher（回傳 `{ ok, ... }`，
   失敗不拋錯），並在 `buildItems` 對應 PSY 行改用新值。
2. 同步更新 `sources-mapping.md`（PSY 編號、端點、可達狀態、missing_reason）。
3. `wrangler deploy` 後等下一個整點，看 `/health` 與 `meta.sources` 確認。
4. 換來源不改契約：item 欄位（psy／asset／x／source_time／fetch_time／
   last_valid_time／window／stale_age_h／is_duplicate／missing_reason）固定；
   絕不在 Worker 內加算分數或符號。

## 已知限制（部署後須驗證，見 sources-mapping.md「未驗證」欄）

1. **OKX rubik 端點未驗證**：long-short-account-ratio（PSY06）、
   taker-volume（PSY34／35／36）是 step0 未列出的端點；抓取失敗即 null，
   部署後看 `meta.sources.okx_rubik_*` 的 HTTP 狀態確認。
2. **Deribit 25Δ 偏態（PSY08）**：期權鏈遍歷為最佳努力（最近到期、現價
   ±15% 內最多 3 call＋3 put，取 |delta| 最接近 0.25 者）；取不到即 null。
3. **GDELT `timespan=1h` 行為未驗證**：程式有防呆（計數 >5000 視為疑似
   非 1h 區間 → null）；部署後須核對計數是否真為 1 小時區間，否則
   PSY11／12／15／16／42／43／60／62 應改回 null。
4. **CoinGecko 免費層限速**：每小時約 8 次呼叫；若頻繁 429，改拉長間隔
   或申請 API key（key 放 Worker secret，見下方）。
5. **Workers 免費版子請求上限**：每執行約 44／50 次；再加來源前先算數，
   超過請升級 Paid（$5／月）或拆成兩次排程。
6. **Workers 免費版 CPU 時間**：以網路等待為主，CPU 只做 JSON 解析；
   若出現 CPU 超限錯誤，升級 Paid 或減少 Deribit 偏態遍歷。
7. **KV 歷史只留 45 天**：180 天基準須靠 `bundle-history/*` 持續累積；
   部署日就是基準起算日，之前無回補（OKX 歷史僅約 5 天，step0 已證實）。

## GitHub Actions 路線（2026-11-27 主人拍板，現行路線）

Cloudflare Workers 版（上文）留作備案，不刪除；現行中繼改走 GitHub Actions，
程式為 `fetch_bundle.py`（標準函式庫為主，無第三方依賴、無任何 key），
排程檔為 `.github/workflows/hourly.yml`。

### 為什麼 repo 必須是公開的

試算表 `Psy64.gs` 用 `UrlFetchApp.fetch` 免認證抓 `bundle.json`。公開 repo 的
raw URL 不需認證；私人 repo 的 raw URL 須帶 token 才能讀，`Psy64.gs` 無法
直接用。repo 名稱定為 `psy64-relay`（帳號 `n9n91555`），raw URL 格式：

```
https://raw.githubusercontent.com/n9n91555/psy64-relay/main/bundle.json
```

### 排程時間

- cron `17 * * * *`：每小時第 17 分（UTC 分鐘＝台北分鐘，時區無關），
  避開整點尖峰（GitHub 排程在尖峰時可能延遲 15 分鐘以上）。
- 每次執行約 2–5 分鐘（Deribit 偏態遍歷與 CoinGecko 循序呼叫為主）；
  job timeout 設 15 分鐘。
- 歷史包寫進 `history/<台北小時>.json`（如 `2026-11-27T12.json`），
  `fetch_bundle.py` 內建清理：超過 45 天的歷史檔自動刪除。

### 手動觸發步驟

1. 進 repo → Actions → 左邊選 `psy64 hourly relay` → 右上 `Run workflow`。
2. 等 job 跑完（約 2–5 分鐘），看 log 末行 `[psy64] bundle ... items=192`。
3. 打開 raw URL 看 `bundle_time` 是否為最新一小時；`null` 數偏高屬正常
   （付費牆／無每小時來源的面向目前皆為 null）。

### 60 天自動停用排程的注意事項與保活方法

- GitHub 會自動停用「60 天完全無活動」的 repo 的排程。
- 本 repo 每小時都有 commit（bundle.json＋history＋state.json），屬持續
  活動，正常不會被停用；若某天發現排程停了（例如連續失敗導致無 commit
  達 60 天），進 Actions 頁按 `Enable` 即可恢復，再手動觸發一次補包。
- 保活的真正關鍵是「每小時 commit 成功」：若 job 連續失敗，先看
  Actions log 修好，不要只靠手動觸發撐著。

### 部署後把 URL 填進 Psy64.gs

把上面 raw URL 填進 `Psy64.gs` 的 `PSY_RELAY_URL`，再把 `Psy64.gs`
貼進試算表綁定專案、授權、執行 `installPsy64Triggers`。
與 Worker 版差異對照見 `github-actions-notes.md`。

## Secrets（有 key 才需要，目前沒有）

```bash
wrangler secret put COINGLASS_API_KEY   # 強平（PSY03／04）有 key 後才啟用
wrangler secret put GLASSNODE_API_KEY   # 鏈上（PSY25 等）有付費方案後才啟用
wrangler secret put LUNARCRUSH_API_KEY  # 社群（PSY09／10）有付費方案後才啟用
```

目前程式碼內沒有任何 key；上述 secret 尚未接線（接線前 PSY03／04／25
等維持 null），有 key 後再改 `getLiquidations`／新增 fetcher 時接上。
