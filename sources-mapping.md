# PSY01–PSY64 Worker 抓取來源對照表

定稿：`verdict.md` §6（凍結定義）。實測基準：`step0-endpoint-check.md`
（2026-11-27，本 VM 出口；Worker 出口不同，部分結果待部署後驗證）。

規則：Worker 只出原始代理值 `x`，不算分數、不寫符號；缺值寫 `null` 並填
`missing_reason`，絕不以 0 代替。標「未驗證」者為 step0 未列出、Worker
首次嘗試的端點或參數，部署後須以 `bundle.json` 的 `meta.sources` 核對。

| PSY | 面向名稱 | Worker 抓取來源／端點 | 可達（step0） | 不可達時的 missing_reason |
|---|---|---|---|---|
| 01 | 恐懼貪婪綜合 | Alternative.me `api.alternative.me/fng/?limit=1`（日更） | 可達 200 | 來源抓取失敗（Alternative.me） |
| 02 | 資金費率過熱 | OKX `public/funding-rate?instId={BTC,ETH}-USDT-SWAP`；XAU null | 可達 200 | 來源抓取失敗（OKX funding-rate）；XAU：黃金無永續資金費率 |
| 03 | 多頭強平恐慌 | edgeornot `edgeornot.com/api/liquidations`（Grok 表引用，未驗證）；Coinglass 無 key 回 500 | 未驗證 | 強平來源須 API key（Coinglass 無 key 回 500）；edgeornot 首次嘗試失敗 |
| 04 | 空頭強平貪婪 | 同 PSY03（空頭強平金額） | 未驗證 | 同 PSY03 |
| 05 | 未平倉興趣急增 | OKX `public/open-interest?instId={BTC,ETH}-USDT-SWAP`，變化率以前一包 state.json 的 OI 算；XAU null | 可達 200 | 來源抓取失敗（OKX open-interest）；首包暖機：無上一小時原始值，無法算變化率；XAU：黃金無永續 OI |
| 06 | 多空比極端 | OKX rubik `rubik/stat/contracts/long-short-account-ratio?instId=…&period=1H` | **未驗證**（step0 未列出） | OKX long-short ratio 端點未驗證／抓取失敗；Binance 多空比地理封鎖（451） |
| 07 | 波動率急升恐懼 | OKX `market/candles?instId={BTC,ETH}-USDT-SWAP&bar=1H` 自算 24 根 1h 報酬標準差；XAU 用 CoinGecko `tether-gold` 小時價 | 可達 200 | 來源抓取失敗（OKX candles／CoinGecko XAUt 小時價） |
| 08 | 期權偏態／尾端風險 | Deribit `get_instruments`＋`ticker`（最近到期、現價 ±15% 內最多 3 call＋3 put，取 \|delta\| 最接近 0.25 者，偏態＝put25IV−call25IV）；XAU null | **未驗證**（遍歷邏輯首次實作；Deribit 本身可達 200） | Deribit 25Δ 偏態未取到（行權價／delta 缺失或逾時）；XAU：黃金無 Deribit 期權偏態 |
| 09 | 社群情緒極性 | LunarCrush／The Tie（須 key） | 須 key／付費牆 | 須 API key／付費牆，免費層無每小時數據（LunarCrush／The Tie 社群情緒極性） |
| 10 | 社群提及量暴增 | LunarCrush／The Tie（須 key） | 須 key／付費牆 | 須 API key／付費牆，免費層無每小時數據（LunarCrush／The Tie 社群提及量） |
| 11 | 新聞恐懼詞頻 | GDELT doc API（`mode=artlist&timespan=1h`，查詢 `bitcoin crash OR bitcoin fear OR bitcoin ban`，取 totalArticles） | 429（step0 從 VM）；**timespan=1h 行為未驗證** | GDELT 抓取失敗／計數不可信（step0 從 VM 429） |
| 12 | 新聞貪婪詞頻 | GDELT doc API（查詢 `bitcoin moon OR bitcoin ATH OR bitcoin bull`） | 同 PSY11 | GDELT 抓取失敗／計數不可信（step0 從 VM 429） |
| 13 | Google 搜尋興趣 | 無（Google Trends 無穩定每小時公開 API） | 429，無每小時 API | 無穩定每小時公開 API（Google Trends 429） |
| 14 | 搜尋崩盤恐懼 | 無（同 PSY13） | 429，無每小時 API | 無穩定每小時公開 API（Google Trends 429） |
| 15 | 敘事「數位黃金」強度 | GDELT doc API（查詢 `"digital gold" bitcoin`） | 同 PSY11 | GDELT 抓取失敗／計數不可信 |
| 16 | 敘事「避險資產」強度 | GDELT doc API（查詢 `"safe haven" gold`） | 同 PSY11 | GDELT 抓取失敗／計數不可信 |
| 17 | 價格對前高錨定距離 | OKX K 線（BTC／ETH）／CoinGecko 小時價（XAU）：(P−H24)／H24 | 可達 200 | 來源抓取失敗（OKX candles／CoinGecko XAUt 小時價） |
| 18 | 價格對前低錨定 | 同 PSY17，(P−L24)／L24 | 可達 200 | 同 PSY17 |
| 19 | 整數關卡錨定 | 同 PSY17，\|P−錨點\|／錨點（BTC 每 1000、ETH 每 100、XAU 每 50） | 可達 200 | 同 PSY17 |
| 20 | 52 週高點錨定 | CoinGecko `market_chart?days=365&interval=daily`：ln(P／H365) | 可達 200 | 來源抓取失敗（CoinGecko 365d） |
| 21 | 均值回歸預期 | 同 PSY20：(P−MA30)／σ30 | 可達 200 | 來源抓取失敗（CoinGecko 365d，日數不足） |
| 22 | 動量追逐強度 | OKX K 線／CoinGecko 小時價：24 根 1h 報酬一階自相關 ρ | 可達 200 | 來源抓取失敗（小時報酬不足 24 根） |
| 23 | 技術突破情緒 | 同 PSY20：突破 30 日高點 +1／跌破 30 日低點 −1／否則 0 | 可達 200 | 來源抓取失敗（CoinGecko 365d，日數不足） |
| 24 | 近因偏誤強度 | OKX K 線／CoinGecko 小時價：sign(r1)·min(\|r1\|／\|r24\|,1) | 可達 200 | 來源抓取失敗（小時價不足 25 根） |
| 25 | 處置效應強度 | Glassnode SOPR（1h 須付費） | 401，須 key | 須 API key／付費牆，免費層無每小時數據（Glassnode SOPR，1h 須付費） |
| 26 | 損失實現率 | Glassnode 鏈上已實現損益（付費） | 401，須 key | 須 API key／付費牆（鏈上已實現損益） |
| 27 | 獲利實現率 | 同 PSY26 | 401，須 key | 須 API key／付費牆（鏈上已實現損益） |
| 28 | 沉沒成本持倉 | Glassnode HODL waves（日更為主） | 日更，須付費 | 無每小時來源（Glassnode HODL waves 日更為主，須付費） |
| 29 | 稟賦效應不願賣 | Glassnode MVRV（日更為主） | 日更，須付費 | 無每小時來源（Glassnode MVRV 日更為主，須付費） |
| 30 | 交易所淨流入 | CryptoQuant／Glassnode | 403／401 | CryptoQuant 403，交易所淨流入免費層未驗證可用 |
| 31 | 交易所淨流出 | 同 PSY30 | 403／401 | CryptoQuant 403，交易所淨流出免費層未驗證可用 |
| 32 | 鯨魚大額轉帳異常 | Whale Alert（須 key） | 須 key | 須 API key／付費牆（Whale Alert 鯨魚轉帳須 API key） |
| 33 | 過度自信槓桿 | OKX 資金費率絕對值（極端值代理，與 PSY02 同源） | 可達 200 | 來源抓取失敗（OKX funding-rate）；XAU：黃金無資金費率 |
| 34 | 從眾買入壓力 | OKX rubik `rubik/stat/taker-volume?instId=…&period=1H`：buy／(buy＋sell) | **未驗證**（step0 未列出） | OKX taker-volume 端點未驗證／抓取失敗；Binance aggTrades 地理封鎖（451） |
| 35 | 從眾賣出壓力 | 同 PSY34：sell／(buy＋sell) | **未驗證** | 同 PSY34 |
| 36 | 損失趨避賣壓 | 同 PSY34（q＝sell／(buy＋sell)，與 PSY35 同一代理值；重複已註明） | **未驗證** | 同 PSY34 |
| 37 | 心理帳戶分離 | OKX 合約 K 線量／（現貨 K 線量＋合約 K 線量） | 可達 200 | 來源抓取失敗（OKX 合約／現貨 K 線）；XAU：黃金無合約／現貨量比 |
| 38 | 時間偏好短視 | 無（OI 期限結構無公開端點） | 無端點 | 無公開端點可取該代理值（OI 期限結構無公開端點） |
| 39 | 後見之明敘事強度 | 無（需 NLP 掃描，未部署；刪除候選，對照組，定義保留，bundle 照樣出 null） | 未部署 | 需 NLP／社群掃描，未部署（後見之明詞頻；刪除候選，對照組，定義保留） |
| 40 | 現狀偏誤不動 | OKX K 線量／24h 量中位數；XAU 用 CoinGecko 小時量（刪除候選，對照組；x 照常出，計分與否由下游檢定決定） | 可達 200 | 來源抓取失敗（OKX candles 量不足／CoinGecko 量不足） |
| 41 | 確認偏誤同溫層 | 社群 API（須 key） | 須 key／付費牆 | 須 API key／付費牆（社群同溫層，須社群 API） |
| 42 | 樂觀偏誤採用 | GDELT doc API（查詢 `"this time different" crypto`） | 同 PSY11 | GDELT 抓取失敗／計數不可信 |
| 43 | 悲觀偏誤崩盤 | GDELT doc API（查詢 `"end of crypto"`） | 同 PSY11 | GDELT 抓取失敗／計數不可信 |
| 44 | 社會證明名人喊單 | X 追蹤列表（無自動源） | 無自動源 | X 追蹤列表須人工／API，無自動事件源 |
| 45 | 權威偏誤機構報告 | 無（事件驅動） | 無自動源 | 事件驅動，無自動事件源（機構研報發布，無自動事件源） |
| 46 | 稀缺性感知 | 無（需 NLP 掃描，未部署） | 未部署 | 需 NLP／社群掃描，未部署（稀缺敘事提及頻率） |
| 47 | 互惠與空投期待 | 社群（須 key） | 須 key／付費牆 | 須 API key／付費牆（空投提及，社群來源付費牆） |
| 48 | 承諾一致性持倉 | 無（人工／半自動；刪除候選，定義保留，bundle 照樣出 null） | 無自動源 | 人工／半自動追蹤，無自動源（刪除候選，定義保留） |
| 49 | FOMO 追高代理 | 複合指標（價格＋社群提及） | 社群付費牆 | 複合指標需社群提及同步，社群來源付費牆 |
| 50 | 恐慌拋售代理 | 複合指標（價格＋強平＋恐懼詞） | 強平須 key | 複合指標需強平數據，強平來源須 API key |
| 51 | 過度反應新聞 | 新聞時間戳＋價格振幅 | 新聞源未部署 | 需新聞時間戳＋價格振幅，新聞源未部署 |
| 52 | 反應不足基本面 | 鏈上活躍地址（Glassnode，付費） | 401，須 key | 須 API key／付費牆（鏈上活躍地址須付費） |
| 53 | 賭徒謬誤連漲後 | OKX K 線／CoinGecko 小時價：連續上漲小時數 | 可達 200 | 來源抓取失敗 |
| 54 | 熱手謬誤連漲繼續 | 同 PSY53（同一連漲小時數；下游符號相反，不在此預設） | 可達 200 | 來源抓取失敗 |
| 55 | 群體極化社群 | 社群 API（須 key） | 須 key／付費牆 | 須 API key／付費牆（極端情緒帖占比，須社群 API） |
| 56 | 資訊瀑布鏈上跟單 | 鏈上數據（付費） | 須 key／付費牆 | 須 API key／付費牆（鏈上跟單，須鏈上數據） |
| 57 | 避險需求強度 | CoinGecko `tether-gold` 小時價：黃金 1h 漲跌幅 r金（三標的皆出同一 r金，下游符號處理） | 可達 200 | 來源抓取失敗（CoinGecko XAUt 小時價） |
| 58 | 美元指數壓力 | 無（DXY 無公開每小時端點） | 無端點 | 無公開端點可取該代理值（DXY 無公開每小時端點，TradingView 無公開 API） |
| 59 | 實質利率預期 | FRED `fredgraph.csv?id=DFII10`：5 日變動（state 累積 6 筆日值，暖機 null） | 逾時（step0 從 VM）；Worker 出口待驗證 | FRED 從本 VM 逾時，Worker 出口待驗證；暖機：FRED 5 日變動需 6 筆日值累積 |
| 60 | 地緣政治風險溢價 | GDELT doc API（查詢 `war OR missile OR airstrike`） | 同 PSY11 | GDELT 抓取失敗／計數不可信 |
| 61 | 黃金恐懼溢價 | 傳統金融數據（部分日更） | 多無每小時來源 | 多無每小時來源（黃金 ETF 流入／GVZ 部分日更） |
| 62 | 監管恐懼強度 | GDELT doc API（查詢 `SEC crypto regulation OR crypto ban`） | 同 PSY11 | GDELT 抓取失敗／計數不可信 |
| 63 | 交易所信任 | CryptoQuant／Nansen（日更為主） | 403／須 key | 日更為主，須付費（CryptoQuant／Nansen） |
| 64 | 穩定幣情緒 | CoinGecko `coins/markets?ids=tether,usd-coin`：USDT＋USDC 市值合計（日更節奏） | 可達 200 | 來源抓取失敗（CoinGecko 穩定幣市值） |

## 備註

- PSY35 與 PSY36 出自同一代理值（OKX taker-volume 的 sell 占比）：定稿
  §6 兩行定義相同，這是定稿原有的重複，不是 Worker 寫錯；檢定時兩行
  會高度相關，屬已知問題，留待 §8 檢定處理（獨立性檢查）。
- PSY53 與 PSY54 出自同一連漲小時數：定稿 §6.1 已註明兩行符號相反、
  下游處理；Worker 只出原始值，不預設方向。
- PSY39／PSY40／PSY48 為定稿「刪除候選」：定義保留、bundle 照樣出
  （PSY40 有可算的 x 照常出；PSY39／PSY48 為 null），計分與否由下游
  Psy64.gs／檢定決定，Worker 不判死刑。
- 黃金（XAU）價格以 CoinGecko `tether-gold`（XAUt）為代理：step0 快照
  已用同一代理（XAUt 3,618.29）；XAUt 是代幣化黃金，非現貨金價本身，
  部署後若要改走 LBMA／TradingView 報價，須另找每小時端點。
- OKX `market/candles` 僅回近期約 100 根（step0：history-candles 僅回
  至約 4–5 天前）：24 根 1h 報酬、30 日均線（PSY21 用日線 365d，不受
  影響）皆可算；180 天基準須靠 KV `bundle-history/*` 自建。
