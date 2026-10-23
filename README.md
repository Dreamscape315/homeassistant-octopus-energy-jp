# Octopus Energy Japan – Home Assistant Integration

[English](#english) | [中文说明](#中文说明)

---

<a name="english"></a>
## English

An unofficial Home Assistant custom integration that brings electricity consumption, cost data, tariff information, and billing details from **Octopus Energy Japan** into Home Assistant.

### Features

- **Direct Web Login**: Sign in using your `octopusenergy.co.jp` account email and password via the UI config flow—no API key required.
- **Multi-Account Support**: Easily select your target account if multiple accounts are associated with your login.
- **Energy Dashboard Ready**:
  - Automatically aggregates half-hourly electricity usage and estimated costs into top-of-the-hour external statistics (`recorder external statistics`), each hour recorded at the time the electricity was actually used.
  - On first run, backfills history from your contract start date (up to 5 years) in the background, gently paced so it doesn't trip the API's rate limits; an interrupted backfill resumes where it stopped.
  - Late-arriving meter data is picked up automatically: every sync re-reads and rewrites the last 48 hours.
  - Add the `Octopus Energy Japan <SPIN> Consumption` / `Cost` statistics under **Settings → Dashboards → Energy**.
- **Comprehensive Sensors (20 Entities)**:
  - **Usage & Cost Tracking**: Half-hourly, today, this week, this month, and total cumulative consumption and cost estimates.
  - **Financial & Billing**: Account balance, overdue balance, and latest bill amount (with billing period and statement dates).
  - **Tariff & Contract Diagnostics**: Contracted capacity (A/kVA), active tariff plan (with tiered pricing breakdown), daily standing charge, fuel cost adjustment, and renewable energy levy.
- **Device Information**:
  - Automatically maps meter serial number, tariff plan name & code, and contracted capacity directly into the Home Assistant device card.

### Sensors Overview

| Sensor | Unit | Type / Class | Description |
|---|---|---|---|
| **Latest Half-Hourly Consumption** | kWh | Energy | Most recent 30-minute electricity usage |
| **Latest Half-Hourly Cost Estimate** | JPY | Monetary | Most recent 30-minute estimated cost |
| **Today's Consumption** | kWh | Energy | Cumulative usage for today (local calendar day) |
| **Today's Cost Estimate** | JPY | Monetary | Estimated cost for today |
| **This Week's Consumption** | kWh | Energy | Cumulative usage for this week (from Monday 00:00) |
| **This Week's Cost Estimate** | JPY | Monetary | Estimated cost for this week |
| **This Month's Consumption** | kWh | Energy | Cumulative usage for this month (from 1st 00:00) |
| **This Month's Cost Estimate** | JPY | Monetary | Estimated cost for this month |
| **Total Consumption** | kWh | Energy (Total Increasing) | Overall usage since the contract start |
| **Total Cost** | JPY | Monetary | Overall estimated cost since the contract start |
| **Account Balance** | JPY | Monetary | Current account balance |
| **Overdue Balance** | JPY | Monetary | Overdue amount |
| **Latest Bill Amount** | JPY | Monetary | Most recent invoice amount with date/period attributes |
| **Account Status** | - | Diagnostic (Enum) | Account status (`active`, `pending`, etc.) |
| **Supply Status** | - | Diagnostic (Enum) | Supply point status (`on_supply`, etc.) |
| **Contracted Capacity** | A / kVA | Diagnostic | Contract capacity (e.g. 30A, 40A) with meter max rating |
| **Tariff Plan** | - | Diagnostic | Current tariff plan with tiered pricing band attributes |
| **Standing Charge** | JPY | Diagnostic | Daily base charge (JPY/day) |
| **Fuel Cost Adjustment** | JPY | Diagnostic | Fuel cost adjustment unit price (JPY/kWh) |
| **Renewable Energy Levy** | JPY | Diagnostic | Renewable energy levy unit price (JPY/kWh) |

### Installation

#### Method 1: HACS Custom Repository (Recommended)

1. Open **HACS** in Home Assistant.
2. Click the three dots in the top-right corner → **Custom repositories**.
3. Enter Repository URL: `https://github.com/Dreamscape315/homeassistant-octopus-energy-jp` and set Category to **Integration**, then click **Add**.
4. Search for **Octopus Energy Japan** in HACS and download it.
5. Restart Home Assistant.

#### Method 2: Manual Installation

1. Download this repository.
2. Copy the `custom_components/octopus_energy_jp` folder into your Home Assistant `<config>/custom_components/` directory.
3. Restart Home Assistant.

### Configuration

1. In Home Assistant, go to **Settings → Devices & Services → Add Integration**.
2. Search for **Octopus Energy Japan**.
3. Enter your Octopus Energy Japan account **email and password**.
4. Set up the **Energy Dashboard**:
   - Go to **Settings → Dashboards → Energy**.
   - Under **Electricity grid**, click **Add consumption** and select **`Octopus Energy Japan <SPIN> Consumption`** (recommended).
   - Under **Track costs**, choose **Use an entity tracking the total costs** and select **`Octopus Energy Japan <SPIN> Cost`**.
   - Why not the `Total Consumption` / `Total Cost` sensors? Octopus meter data arrives hours late. The `<SPIN>` statistics are written by the integration with each reading's real time, while Home Assistant's statistics for a sensor book usage at the moment the late data arrived, shifting your hourly graph by several hours.
5. The history backfill runs in the background after setup and may take a few minutes (one request every 15 seconds); the Energy Dashboard fills in as it progresses.

### Acknowledgements

- [bottlecapdave/HomeAssistant-OctopusEnergy](https://github.com/bottlecapdave/HomeAssistant-OctopusEnergy) - Home Assistant integration for Octopus Energy UK.
- [caru-ini/octopus-bot](https://github.com/caru-ini/octopus-bot) - Reference implementation for Octopus Energy Japan Kraken GraphQL API authentication and queries.

---

<a name="中文说明"></a>
## 中文说明

这是一个非官方的 Home Assistant 自定义集成，用于将 **Octopus Energy Japan** 的用电数据、费用估算、资费详情与账单信息无缝接入 Home Assistant。

### 功能特性

- **直接使用官网账号登录**：支持在 UI 配置向导中直接输入 `octopusenergy.co.jp` 的官网邮箱与密码登录，无需申请 API Key。
- **支持多账户管理**：如果一个账号下拥有多个电力账户，配置时支持交互式选择。
- **深度适配 Energy Dashboard（能源仪表盘）**：
  - 自动将半小时用电量及预估费用按整点聚合后写入 Home Assistant 外部长期统计（`recorder external statistics`），每小时的用量记在实际用电的时间。
  - 首次运行时从合同开始日期起（最多 5 年）在后台回填历史数据，请求节奏放缓，避免触发 API 限流；中断后会从断点继续。
  - 自动补上延迟上报的电表数据：每次同步都会重新读取并覆盖最近 48 小时。
  - 在 **设置 → 仪表盘 → 能源** 中选择 `Octopus Energy Japan <SPIN> Consumption` / `Cost` 统计即可。
- **丰富的传感器体系（共 20 个实体）**：
  - **周期用电与费用**：最近半小时、今日、本周、本月及自接入以来的总累计用电量与预估费用。
  - **财务与账单**：账户余额、逾期未缴金额、最新账单金额（含账单周期与开票日属性）。
  - **资费与合约诊断**：契约容量（A/kVA）、当前资费方案（含各阶梯电价明细）、基本料金、燃料费调整单价、再エネ賦課金。
- **设备详情整合**：
  - 在设备卡片中直接显示电表计器番号（`serial_number`）、资费方案名（`model`）、代码（`model_id`）及契约容量（`hw_version`）。

### 传感器列表一览

| 传感器名称 | 单位 | 类别 / Class | 说明 |
|---|---|---|---|
| **Latest Half-Hourly Consumption** | kWh | Energy | 最近一条已出结果的半小时用电量 |
| **Latest Half-Hourly Cost Estimate** | JPY | Monetary | 对应的预估电费 |
| **Today's Consumption** | kWh | Energy | 今日累计用电量（本地日历日 0 点起） |
| **Today's Cost Estimate** | JPY | Monetary | 今日累计预估电费 |
| **This Week's Consumption** | kWh | Energy | 本周累计用电量（周一 0 点起） |
| **This Week's Cost Estimate** | JPY | Monetary | 本周累计预估电费 |
| **This Month's Consumption** | kWh | Energy | 本月累计用电量（当月 1 号 0 点起） |
| **This Month's Cost Estimate** | JPY | Monetary | 本月累计预估电费 |
| **Total Consumption** | kWh | Energy (Total Increasing) | 自合同开始以来的总累计用电量 |
| **Total Cost** | JPY | Monetary | 自合同开始以来的总累计预估费用 |
| **Account Balance** | JPY | Monetary | 账户余额（整円） |
| **Overdue Balance** | JPY | Monetary | 逾期未缴金额 |
| **Latest Bill Amount** | JPY | Monetary | 最近一张账单应付总额（含开票日与账单周期属性） |
| **Account Status** | - | Diagnostic (Enum) | 账户状态（`active`、`pending` 等） |
| **Supply Status** | - | Diagnostic (Enum) | 供电状态（`on_supply` 等） |
| **Contracted Capacity** | A / kVA | Diagnostic | 契约容量（如 30A/40A，含电表额定容量） |
| **Tariff Plan** | - | Diagnostic | 当前生效资费方案名称（属性附带各阶梯电价明细） |
| **Standing Charge** | JPY | Diagnostic | 基本料金（円/日） |
| **Fuel Cost Adjustment** | JPY | Diagnostic | 燃料费调整额单价（円/kWh） |
| **Renewable Energy Levy** | JPY | Diagnostic | 再エネ賦課金单价（円/kWh） |

### 安装方法

#### 方式一：通过 HACS 自定义存储库安装（推荐）

1. 打开 Home Assistant 的 **HACS** 页面。
2. 点击右上角菜单（三个点）→ **自定义存储库（Custom repositories）**。
3. 输入存储库 URL：`https://github.com/Dreamscape315/homeassistant-octopus-energy-jp`，类别选择 **集成（Integration）**，点击添加。
4. 在 HACS 列表中搜索 **Octopus Energy Japan** 并下载。
5. 重启 Home Assistant。

#### 方式二：手动安装

1. 下载本仓库，将 `custom_components/octopus_energy_jp` 目录拷贝到你的 Home Assistant 配置目录下的 `custom_components/` 文件夹中。
2. 重启 Home Assistant。

### 配置与使用

1. 在 Home Assistant 中进入 **设置 → 设备与服务 → 添加集成**。
2. 搜索 **Octopus Energy Japan**。
3. 输入你的 Octopus Energy Japan 官网**邮箱和密码**完成添加。
4. 进入 **设置 → 仪表盘 → 能源**：
   - 在 **电网消耗** 中点击 **添加消耗**，选择 **`Octopus Energy Japan <SPIN> Consumption`**（推荐）；
   - 在 **追踪成本** 中选择 **使用条目跟踪成本**，选择 **`Octopus Energy Japan <SPIN> Cost`**。
   - 为什么不推荐 `Total Consumption` / `Total Cost` 实体？Octopus 的电表数据会延迟几个小时才到。`<SPIN>` 统计由集成按每条读数的真实时间写入；而实体的统计是 Home Assistant 按"数据到达的时刻"记账的，整条小时曲线会往后错开几个小时。
5. 添加完成后，历史数据会在后台回填（每 15 秒一次请求），可能需要几分钟，能源仪表盘会随之逐步补全。

### 鸣谢与参考

- [bottlecapdave/HomeAssistant-OctopusEnergy](https://github.com/bottlecapdave/HomeAssistant-OctopusEnergy) - 英国官方社区的 Home Assistant Octopus Energy 集成。
- [caru-ini/octopus-bot](https://github.com/caru-ini/octopus-bot) - 日本站 Kraken GraphQL API 认证机制与调用的参考实现。
