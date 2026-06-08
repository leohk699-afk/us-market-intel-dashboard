# 美股宏观情报员 Dashboard V1

这是一个低成本、可迭代的美股宏观情报网站原型：

- `index.html`：静态仪表盘页面
- `assets/`：前端样式与交互逻辑
- `data/latest.json`：网站读取的最新市场状态
- `data/history.json`：历史风险分记录
- `scripts/fetch_data.py`：每日抓取数据、计算风险分、生成 JSON
- `config/indicators.yml`：指标注册表，后续新增指标主要改这里
- `.github/workflows/update-data.yml`：GitHub Actions 定时任务

## 1. 本地预览

```bash
cd us_market_intel_dashboard
python -m http.server 8000
```

浏览器打开：

```text
http://localhost:8000
```

## 2. 本地更新数据

安装依赖：

```bash
pip install -r requirements.txt
```

可选环境变量：

```bash
export FRED_API_KEY="你的FRED API Key"
export ALPHA_VANTAGE_API_KEY="你的Alpha Vantage API Key"
```

执行：

```bash
python scripts/fetch_data.py
```

脚本会生成：

```text
data/latest.json
data/history.json
```

## 3. 部署到 GitHub Pages

1. 新建 GitHub 仓库，例如：`us-market-intel-dashboard`。
2. 上传整个项目。
3. 在仓库 Settings → Secrets and variables → Actions 中新增：
   - `FRED_API_KEY`
   - `ALPHA_VANTAGE_API_KEY`，可选，用于 ETF/股票价格。
4. 到 Actions 页面，手动运行 `Update market intelligence data`。
5. 到 Settings → Pages，选择部署源。简单模式可选择根目录；进阶模式可接入 GitHub Actions Pages 部署。
6. 打开 GitHub Pages 链接，即可查看网站。

## 4. 自动更新频率

`.github/workflows/update-data.yml` 默认在美股交易日每天 13:05 UTC 执行一次，大致等于美国太平洋时间夏令时 06:05。你可以改成：

- 盘前：`5 13 * * 1-5`
- 盘后：`30 22 * * 1-5`
- 每天两次：增加第二条 cron

GitHub Actions 的 schedule 使用 POSIX cron，并且默认按 UTC 运行。

## 5. 如何迭代

### 新增宏观指标

在 `config/indicators.yml` 增加一个 FRED 指标：

```yaml
- id: example
  label: Example Indicator
  source: fred
  series: FRED_SERIES_ID
  unit: "%"
  positive_is_risk: true
```

然后在 `scripts/fetch_data.py` 的 `risk_score()` 中补一个阈值规则。

### 新增市场价格指标

当前 V1 通过 Alpha Vantage 支持 ETF/股票日线。你也可以替换为 Polygon、Financial Modeling Prep、Twelve Data、Quandl/Nasdaq Data Link 等授权数据源。

### 新增预警

后续可以增加：

- 邮件/Telegram/微信推送
- 指标突破阈值提醒
- CPI/FOMC/非农数据日提醒
- 个股池联动：NVDA、TSLA、PLTR、MSTR、COIN 等

## 6. 当前限制

- V1 是规则模型，不是机器学习模型。
- 风险分阈值是启发式，需要用历史回测逐步校准。
- 部分市场数据需要授权 API，不能长期依赖非正式数据源。
- 本项目用于个人研究和市场监测，不构成投资建议。
