# Stock Query - A股数据查询与买入评估工具

基于新浪财经 API 的 A 股数据服务与多策略买入评估技能包。

> 项目安装说明、快速开始、常见问题请参阅 [README.md](README.md)

## 技能概述

| 项目 | 说明 |
|------|------|
| 技能名称 | StockSinaService |
| 版本 | 1.0.0 |
| 核心功能 | A股实时行情查询、历史K线获取、K线图绘制、多策略买入评估 |
| 数据来源 | 新浪财经 API (`vip.stock.finance.sina.com.cn`) |

---

## 触发场景

当用户出现以下需求时，应该使用此技能包：

- ✅ 查询沪深 A 股实时行情（价格、涨跌幅、成交量等）
- ✅ 获取股票历史 K 线数据（日线、周线、月线、分钟线）
- ✅ 绘制股票 K 线蜡烛图
- ✅ 评估某只股票是否值得买入（多策略评分）
- ✅ 批量筛选符合条件的推荐股票（按得分排序）

---

## 使用前提

| 项目 | 要求 |
|------|------|
| Python | >= 3.8 |
| 依赖 | `requests`, `pandas`, `numpy`, `mplfinance`, `matplotlib` |
| 网络 | 需能访问 `vip.stock.finance.sina.com.cn` |

安装依赖：

```bash
pip install -r requirements.txt
```

---

## 核心功能

### 功能一：获取股票列表与实时行情

```python
from stock_service import StockSinaService

sina = StockSinaService()

# 获取沪深A股完整列表（约5000+只）
stock_list = sina.get_stock_list()

# 获取大盘指数列表
dpzs_list = sina.get_stock_list(node='dpzs')
```

返回字段说明：

| 字段 | 说明 | 示例 |
|------|------|------|
| symbol | 带前缀代码 | `"sh600000"` |
| code | 纯数字代码 | `"600000"` |
| name | 股票名称 | `"浦发银行"` |
| trade | 当前价（元） | `"8.15"` |
| changepercent | 涨跌幅(%) | `"0.617"` |
| volume | 成交量（股） | `"52381600"` |
| per | 市盈率 PE | `"4.893"` |
| pb | 市净率 PB | `"0.412"` |
| mktcap | 总市值（万元） | `"23920580"` |

---

### 功能二：获取历史 K 线数据

```python
# 按 symbol 获取（带交易所前缀）
# scale: 1/5/15/30/60分钟, 240日线, 1200周线, 7200月线
data = sina.get_stock_history_data('sh600000', scale=240, datalen=200)

# 按纯数字代码自动匹配交易所
data = sina.get_stock_history_data_bycode('600000', scale=240, datalen=200)
```

返回字段说明：

| 字段 | 说明 |
|------|------|
| day | 时间 `"2026-04-10 11:30:00"` |
| open/high/low/close | 开盘价/最高价/最低价/收盘价 |
| volume | 成交量 |
| ma_price{5/10/20/60} | 对应周期均价 |
| ma_volume{5/10/20} | 对应周期均量 |

---

### 功能三：绘制 K 线图

```python
# 获取并绘制 K 线蜡烛图 + 成交量图（弹出 matplotlib 窗口）
sina.get_stock_kline('sh600000', scale=60, datalen=200)
```

---

### 功能四：买入评估（核心功能）

```python
# 综合评估（默认：strategy='all', risk='balanced'）
stock_list = sina.get_stock_list()
result = sina.should_buy('sh600000', stock_info=stock_list[0])

# 指定策略和风险等级
result = sina.should_buy('sh600519', strategy='technical', risk='aggressive')
```

### 功能五：批量筛选股票建议（stock_suggestion 方法）

```python
# 获取得分 >= 60 的推荐股票（默认返回前 10 只）
suggestions = sina.stock_suggestion(top=10, score=60, risk='balanced')

# 保守型筛选（得分 >= 70）
suggestions = sina.stock_suggestion(top=20, score=70, risk='conservative')

# 激进型筛选（得分 >= 50）
suggestions = sina.stock_suggestion(top=20, score=50, risk='aggressive', strategy='technical')
```

#### stock_suggestion 方法说明

本方法会：
1. 并发获取全量 A 股行情列表
2. 多线程并发评估所有股票的买入评分
3. 按得分降序排列，返回前 top 个推荐

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| top | int | 10 | 返回建议的股票数量 |
| score | int | 60 | 最低筛选分数（稳健型） |
| risk | str | 'balanced' | 风险偏好 |
| strategy | str | 'all' | 评分策略 |

| 返回 | 类型 | 说明 |
|------|------|------|
| list[dict] | 股票建议列表 | 按得分降序排列，每项包含 symbol/score/decision/reasons/current_price |

#### 评估策略

| 策略 | 参数值 | 分析维度 |
|------|--------|----------|
| 技术面 | `technical` | MA均线、成交量、支撑阻力、ATR波动率、突破 |
| 基本面 | `fundamental` | PE市盈率、PB市净率、市值规模、流通占比 |
| 趋势跟踪 | `trend` | MACD、金叉死叉、RSI、均线多头排列 |
| 综合模式 | `all`（默认） | 以上三种策略加权合并 |

#### 风险等级

| 风���等级 | 参数值 | 推荐购买阈值 |
|----------|--------|-------------|
| 保守 | `conservative` | >= 70 分 |
| 稳健 | `balanced`（默认） | >= 60 分 |
| 激进 | `aggressive` | >= 50 分 |

#### 返回结果示例

```json
{
  "symbol": "sh600000",
  "strategy": "all",
  "risk": "balanced",
  "score": 68.5,
  "decision": "推荐购买",
  "reasons": [
    "== 技术面 ==",
    "价格在20日均线之上 (+15)",
    "价格在60日均线之上 (+15)",
    "近5日上涨 3.2% (+10)",
    "== 趋势跟踪 ==",
    "MACD金叉 (+20)",
    "RSI=55.3，中性健康区间 (+20)",
    "== 基本面 ==",
    "PE=4.9，估值合理 (+20)",
    "PB=0.41，破净或接近净资产 (+20)"
  ],
  "current_price": 8.15
}
```

### 功能六：获取建议列表（命令行）

```bash
# 获取前 10 只推荐股票（得分 >= 60）
python stock_service.py --suggest

# 保守型筛选（得分 >= 70，返回前 20 只）
python stock_service.py --suggest --top 20 --score 70 --risk conservative
```

---

## 策略详解

### 技术面策略 (technical)

基于 K 线价量关系的短中期分析，7 个评分维度：

| 维度 | 指标说明 | 激进得分条件 |
|------|----------|-------------|
| MA20 位置 | 价格与 20 日均线的关系 | 站上 MA20 +15 |
| MA60 位置 | 价格与 60 日均线的关系 | 站上 MA60 +15 |
| 5日涨跌幅 | 最近 5 个交易日的累计收益率 | >1% +12 |
| 成交量 | 5日均量/20日均量 | >1.2倍 +12 |
| 支撑/阻力 | 当前价与 20 日低点的距离 | <=1.05低点 +12 |
| ATR波动率 | 14 日真实波幅/价格 | <5% +5 |
| 突破迹象 | 收盘价是否突破前 5 日最高价 | 突破 +15 |

### 基本面策略 (fundamental)

基于估值和市值的价值分析（需要 `stock_info` 参数）：

| 维度 | 指标说明 | 激进得分条件 |
|------|----------|-------------|
| PE 市盈率 | 股价/每股收益 | 0<PE<50 +15 |
| PB 市净率 | 股价/每股净资产 | PB<5 +15 |
| 市值规模 | 总市值 | 不限 +20 |
| 流通占比 | 流通市值/总市值 | 不限 +15 |

### 趋势跟踪策略 (trend)

基于经典技术指标的趋势强度判断：

| 维度 | 指标说明 | 激进得分条件 |
|------|----------|-------------|
| MACD | DIF 与 DEA 的交叉关系 | 金叉或即将金叉 +20 |
| 布林带 | 价格在通道中的位置 | 突破上轨 +20 |
| RSI(14) | 相对强弱指标 | 50-80 强势区间 +20 |
| 均线排列 | 短中长期 MA 的顺序 | MA5>MA10 +20 |

---

## 命令行用法

除了作为模块调用，也可以直接使用命令行：

```bash
# 获取股票总数
python stock_service.py --count

# 获取全部A股列表
python stock_service.py --list

# 获取K线数据
python stock_service.py --history sh600000 --scale 240 --datalen 200

# 绘制K线图
python stock_service.py --kline sh600000

# 买入评估
python stock_service.py --score sh600000 --strategy all --risk balanced

# 批量筛选推荐股票
python stock_service.py --suggest
python stock_service.py --suggest --top 20 --score 70 --risk conservative
```

---

## 注意事项

1. **网络要求** — 需能访问新浪财经 API，被限流时会自动重试 3 次
2. **基本面策略** — 必须传入 `stock_info` 参数（包含 PE、PB、市值等数据）
3. **综合模式** — 有 `stock_info` 时按技术面40%+基本面30%+趋势30%计算，无 `stock_info` 时按技术面50%+趋势50%计算
4. **K 线图** — 绘制时会弹出 matplotlib 窗口（阻塞），关闭后程序继续
5. **数据延迟** — 新浪数据有 15 分钟延迟，实时交易需注意

---

## 免责声明

本工具仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `stock_service.py` | 命令行入口 |
| `sina_service.py` | 核心服务类 |
| `requirements.txt` | Python 依赖 |
| `README.md` | 项目说明文档 |