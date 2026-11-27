# GitHub Actions 版與 Worker 版差異對照

現行路線：GitHub Actions（`fetch_bundle.py`＋`.github/workflows/hourly.yml`）。
備案：Cloudflare Workers（`src/index.js`＋`wrangler.toml`，不刪除）。
兩版來源清單、契約欄位、缺值處理皆一致（Python 版為 Worker 版逐行移植）；
差異只在執行環境與保存方式。

## 差異對照

| 項目 | Worker 版 | GitHub Actions 版 |
|---|---|---|
| 觸發 | cron 每小時整點 `0 * * * *` | cron 每小時第 17 分 `17 * * * *`（避開整點尖峰） |
| 準時性 | 整點觸發，誤差約分鐘級 | 排程為 best-effort；尖峰時可能延遲 15 分鐘以上，偶有跳過 |
| 執行上限 | 每次 10ms CPU／50 次子請求（免費版，實測約 44 次，很緊） | 無子請求數上限；job 最長 6 小時，timeout 設 15 分鐘 |
| 單一來源逾時 | 8 秒 | 10 秒（Deribit ticker 5 秒） |
| 並行 | JS Promise.all | ThreadPoolExecutor（第一批 12 併發；CoinGecko 循序＋300ms 間隔） |
| 最新一包存放 | KV `bundle.json` | repo 根目錄 `bundle.json`（公開 raw URL） |
| 歷史保存 | KV `bundle-history/<小時>`，45 天 TTL | `history/<台北小時>.json`，腳本內建清理（超過 45 天刪除） |
| 上一小時狀態 | KV `state.json` | repo 根目錄 `state.json`（隨 bundle 一起 commit） |
| 對外讀取 | `GET /bundle.json`（workers.dev） | `https://raw.githubusercontent.com/n9n91555/psy64-relay/main/bundle.json` |
| 認證／授權 | 須 Cloudflare 帳號＋wrangler 登入；部署須寫入權限 | 須 GitHub repo（帳號 `n9n91555` 已有）；`GITHUB_TOKEN`＋`contents: write` 即可，無額外授權 |
| 帳號需求 | Cloudflare 帳號（免費，不綁卡） | GitHub 帳號（已有）；repo 必須公開，試算表才能免認證抓 raw URL |
| 失敗可見性 | `wrangler tail` 即時 log | Actions 頁每 run 的 log；失敗會標紅，可重跑 |
| 排程停用風險 | 無（cron 一直跑） | 60 天完全無活動的 repo 會被自動停用排程；本 repo 每小時 commit，正常不會停；停用後進 Actions 頁按 Enable 恢復 |
| 成本 | 免費版額度內 | 公開 repo runner 免費不限分鐘；私人 repo 每月 2,000 分鐘免費（本 job 每小時約 2–5 分鐘，一個月約 1,500–3,600 分鐘，私人 repo 可能超額——所以用公開 repo） |
| 暖機 | 部署日為 180 天基準起算日 | 相同：第一包起算；PSY05（OI 變化率）與 PSY59（FRED 5 日變動）首包為 null，屬正常 |

## 部署後要驗的清單（與 Worker 版同一批未驗證項）

1. **OKX rubik 端點**：`long-short-account-ratio`（PSY06）、`taker-volume`
   （PSY34／35／36）是 step0 未列出的端點；看 `bundle.json` 的
   `meta.sources.okx_rubik_lsr`／`okx_rubik_taker` 的 HTTP 狀態與
   `PSY06／34／35／36` 是否仍為 null。
2. **Deribit 25Δ 偏態（PSY08）**：期權鏈遍歷為最佳努力（最近到期、現價
   ±15% 內最多 3 call＋3 put，取 |delta| 最接近 0.25 者）；看
   `meta.sources.deribit_skew_btc` 與 PSY08 是否有值。
3. **GDELT `timespan=1h` 行為**：程式有防呆（計數 >5000 視為疑似非 1h
   區間 → null）；部署後核對 PSY11／12／15／16／42／43／60／62 的計數
   是否真為 1 小時區間（合理值通常為個位數至數十），否則應改回 null。
4. **edgeornot 強平端點（PSY03／04）**：URL 引自 Grok 交辯表，step0 未測；
   看 `meta.sources.edgeornot_liq` 狀態；失敗即 null，Coinglass 仍須 key。
5. **FRED DFII10（PSY59）**：step0 從本 VM 逾時；看
   `meta.sources.fred_dfii10` 在 Actions 出口是否通；PSY59 需累積 6 筆
   日值才有值，前 5 天為 null 屬正常。
6. **CoinGecko 429**：每小時約 8 次呼叫、間隔 300ms；若
   `meta.sources.coingecko_*` 頻繁 429，拉長間隔或申請 API key
   （key 放 repo secret，程式目前未接線）。
7. **排程準時性**：連續觀察幾小時 `bundle_time` 是否都在每小時第 17 分
   後不久（約 17–25 分）；若常延遲超過 30 分鐘或跳過，考慮改回 Worker
   版或改排程分鐘。
8. **每小時 commit**：確認 Actions 每次 run 都有新 commit（bundle_time
   更新）；若連續無 commit，先看 log 修好，不要只靠手動觸發撐著——
   這也是 60 天自動停用的保活關鍵。

## 已知重複（定稿原有，非程式錯誤）

- PSY35 與 PSY36 出自同一代理值（OKX taker-volume 的 sell 占比）。
- PSY53 與 PSY54 出自同一連漲小時數（下游符號相反，不在此預設）。
- 黃金（XAU）價格以 CoinGecko `tether-gold`（XAUt）為代理（代幣化黃金，
  非現貨金價本身）；step0 快照已用同一代理。
