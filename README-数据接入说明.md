# 金鉴 · 免费数据源接入说明

## 实时化三档方案（按复杂度递增）

**Tier A（已实现，无需部署）**：金价15秒轮询 + 价格跳动闪烁效果 + 浏览器系统通知
（大起大落预警、高影响事件倒计时归零时会弹通知，需要你打开页面时点一下"允许通知"）。

**Tier B（本README这部分，免费但要部署一次）**：用 GitHub Actions 定时任务，
每15分钟自动帮你跑 `fetch_gold_data.py`，不需要你自己电脑一直开着脚本。
配合 GitHub Pages 免费托管，手机上随时打开都是新数据。步骤见下方"部署到GitHub"。

**Tier C（要花钱，本文件不包含）**：换成付费WebSocket实时数据源
（如Polygon.io、Finnhub付费档）+ 常驻云函数做流转发，是从"个人工具"到"产品"的台阶，
个人使用一般不需要走到这一步。

---

## 可选：接入 GoldAPI.io（更精确的涨跌幅/最高/最低数据）

**重要安全提醒**：GoldAPI.io官方文档明确写着"不要在浏览器JS里暴露私有token"——
因为部署上线的网页是公开的，任何人查看网页源代码都能看到写死在里面的Key，
拿去用会把你的免费额度消耗掉。所以这个Key **绝对不能** 直接粘贴进`gold-dashboard.html`里。

正确做法是存成 GitHub 仓库的加密 Secret，只在服务器端（GitHub Actions）使用：

1. 进仓库 Settings → 左侧菜单 "Secrets and variables" → "Actions"
2. 点 "New repository secret"
3. Name 填：`GOLDAPI_KEY`
4. Value 填：你的GoldAPI.io Key（形如 `goldapi-xxxxxxxxxxxx-io`）
5. 点 Add secret 保存

保存后，之前部署的 GitHub Actions workflow 会自动读取这个 Secret 并在每次抓取时使用，
Key不会出现在任何公开文件、提交记录或网页源代码里。

不设置这个 Secret 完全没问题，`fetch_gold_data.py` 会自动跳过这部分，
其他数据（金价、DXY、VIX、原油、利率）照常抓取更新。

---

## 部署到 GitHub，实现 Tier B 自动化

1. 在 GitHub 建一个新仓库，把 `gold-dashboard.html`、`fetch_gold_data.py`、
   `.github/workflows/update-gold-data.yml` 这几个文件都传上去（保持目录结构，
   workflow文件必须在 `.github/workflows/` 路径下才会被GitHub识别）
2. 仓库 Settings → Actions → General，确认Actions是开启状态
3. 仓库 Settings → Pages，Source选你的分支（一般是main），保存后会给你一个
   `https://你的用户名.github.io/仓库名/gold-dashboard.html` 的免费网址
4. 等15分钟，或者去仓库的 Actions 标签页手动点"Run workflow"立即触发一次
5. 跑完之后，仓库里会多一个自动提交的 `gold-data.json`，页面读到它就会显示"全部实时"

之后完全不需要你管，GitHub会一直按15分钟的节奏帮你更新，免费额度对这个用量绰绰有余。

---

## 好消息：金价已经是真·实时了，不需要跑任何脚本

`gold-dashboard.html` 现在会在打开页面时自动直接向 `https://api.gold-api.com/price/XAU`
发起请求（这个API完全免费、不需要注册、而且官方明确开启了CORS，允许浏览器直接跨域调用）。

也就是说：**你现在直接双击打开 `gold-dashboard.html`，金价就是实时的**，
每60秒自动刷新一次，页面顶部状态条会显示"金价：实时"。这一步不需要下面的 Python 脚本、
不需要服务器、不需要任何配置。

下面这套 `fetch_gold_data.py + gold-data.json` 方案，是给 **DXY/VIX/原油/利率** 这几个
没法直接在浏览器里跨域调用的数据源用的（Yahoo Finance、FRED都不支持浏览器直连），
这部分仍然需要你在本地跑一下脚本才能同步。

---

## 这是做什么的

`fetch_gold_data.py` 每次运行会从三个完全免费、不需要信用卡的数据源拉取数据，
写入同目录下的 `gold-data.json`。仪表盘 HTML 页面里已经加了一段 JS
（`loadLiveData()`），页面打开时会自动尝试读取这个文件：

- 读到了 → 用真实数据覆盖金价/DXY/VIX/原油，数据源状态条自动变成"实时"
- 读不到（文件不存在、或还没跑过脚本）→ 静默保留演示数据，页面照常显示，不会报错

## 第一步：安装依赖并跑一次

```bash
pip install requests yfinance --break-system-packages
python3 fetch_gold_data.py
```

跑完你会看到同目录多了一个 `gold-data.json`，内容大概长这样（可以参考 `gold-data.example.json`）：

```json
{
  "fetchedAt": "2026-07-04T11:42:00+00:00",
  "gold": { "price": 4170.30, "updatedAt": "..." },
  "dxy": { "value": 100.78, "changePct": -0.08 },
  ...
}
```

## 第二步：把 gold-data.json 和 gold-dashboard.html 放同一个目录

页面用相对路径 `./gold-data.json` 去读文件，所以两个文件必须在同一目录下。

## ⚠️ 重要：本地直接双击打开 HTML 是读不到数据的

浏览器出于安全限制，`file://` 协议下的网页默认不允许用 `fetch()` 读取本地文件
（会报 CORS 错误）。有两个解决办法：

**方案A（本地预览用）**：在该目录下起一个本地小服务器
```bash
python3 -m http.server 8000
```
然后浏览器打开 `http://localhost:8000/gold-dashboard.html` 就能正常读到数据了。

**方案B（长期用，推荐）**：把这两个文件一起部署到 Cloudflare Pages / Vercel /
GitHub Pages 这类免费静态托管上，线上访问天然没有这个限制。

## 第三步：让它自动定时更新

个人用的话，最简单的是本机 cron（Mac/Linux）：

```bash
# 编辑 crontab
crontab -e

# 加一行：每15分钟跑一次
*/15 * * * * cd /你的路径/gold-data-fetcher && /usr/bin/python3 fetch_gold_data.py
```

如果部署在 GitHub Pages，可以用 **GitHub Actions 的 schedule 触发器**，
每15分钟自动跑一次脚本、把新的 `gold-data.json` commit 回仓库，
完全免费、不需要自己的服务器一直开着。需要的话我可以再帮你写这个 workflow 文件。

## 关于数据源的免责说明

- gold-api.com、yfinance(Yahoo Finance) 都不是官方持牌数据商，偶尔可能限流或调整接口，
  个人使用没问题，但不建议做成对外收费的产品
- FRED 数据更新有延迟（如10年期实际利率是每个交易日更新一次，不是分钟级）
- 这套方案没有覆盖期权数据(IV/Max Pain/PC比)，这块目前没有靠谱的免费选项，
  暂时只能继续用演示数值
