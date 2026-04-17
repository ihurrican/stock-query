# Stock Query

A股数据服务与多策略买入评估工具。

基于新浪财经 API，提供沪深 A 股实时行情查询、历史 K 线获取、K 线图绘制，以及多策略买入评分系统。

> 完整 API 文档见 [skill.md](skill.md)

---

## 功能特性

- **实时行情** — 并发拉取 5000+ 只 A 股的最新行情数据
- **历史 K 线** — 支持分钟线/日线/周线/月线
- **K 线图绘制** — 标准蜡烛图 + 成交量图
- **多策略评估** — 技术面 / 基本面 / 趋势跟踪 / 综合模式
- **多风险等级** — 保守 / 稳健 / 激进 三档
- **批量筛选** — 按评分筛选符合条件的推荐股票
- **高性能** — 连接池复用、自动重试、20 线程并发

---

## 环境要求

| 项目 | 要求 |
|------|------|
| Python | >= 3.8 |
| 操作系统 | Windows / macOS / Linux |
| 网络 | 需能访问 `vip.stock.finance.sina.com.cn` |

---

## 安装

### 1. 克隆项目

```bash
git clone <仓库地址>
cd stock-query
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

国内用户可使用清华镜像源加速：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 验证安装

```bash
python -c "import requests, pandas, numpy, mplfinance, matplotlib; print('OK')"
```

---

## 快速开始

### 命令行运行

扫描全部 A 股，输出推荐购买的股票：

```bash
python stock_service.py --list                    # 获取全部A股行情
python stock_service.py --count                   # 获取股票总数
python stock_service.py --history sh600000       # 获取K线数据
python stock_service.py --kline sh600000          # 绘制K线图
python stock_service.py --score sh600000          # 买入评估
python stock_service.py --suggest                # 批量筛选推荐股票
python stock_service.py --suggest --top 20      # 筛选前20只
```

### 作为模块使用

```python
from stock_service import StockSinaService

sina = StockSinaService()

# 获取行情列表
stock_list = sina.get_stock_list()
print(f"共获取 {len(stock_list)} 只股票")

# 评估单只股票
result = sina.should_buy('sh600000', stock_info=stock_list[0])
print(f"决策: {result['decision']}, 得分: {result['score']}")

# 绘制K线图（会弹出窗口）
sina.get_stock_kline('sh600000', scale=60, datalen=200)

# 批量筛选推荐股票（stock_suggestion 方法）
suggestions = sina.stock_suggestion(top=10, score=60, risk='balanced')
for s in suggestions:
    print(f"{s['symbol']} 得分:{s['score']:.1f}")
```

---

## 评估策略一览

### 三种策略

| 策略 | 参数值 | 分析维度 |
|------|--------|----------|
| 技术面 | `technical` | MA均线、成交量、支撑阻力、ATR波动率 |
| 基本面 | `fundamental` | PE市盈率、PB市净率、市值规模 |
| 趋势跟踪 | `trend` | MACD、布林带RSI、均线多头排列 |
| 综合 | `all` | 以上三种加权合并 |

### 三档风险

| 风险等级 | 推荐购买阈值 |
|----------|-------------|
| 保守 | >= 70 分 |
| 稳健（默认） | >= 60 分 |
| 激进 | >= 50 分 |

---

## 项目结构

```
stock-query/
  stock_service.py      # 命令行入口 + StockSinaService 类
  sina_service.py     # 核心服务实现
  requirements.txt   # Python 依赖清单
  skill.md          # API 技能包文档
  README.md        # 本文件
```

---

## 常见问题

**Q: `ModuleNotFoundError: No module named 'mplfinance'`**

```bash
pip install -r requirements.txt
```

**Q: 控制台中文乱码**

```bash
# Windows
set PYTHONIOENCODING=utf-8

# Linux / macOS
export PYTHONIOENCODING=utf-8
```

**Q: 大量请求超时**

程序已内置 3 次自动重试。若持续超时，可能被新浪限流，稍后重试即可。

**Q: K 线图中文显示为方块**

- **Windows** — 通常预装微软雅黑，无需操作
- **macOS** — 在代码中将 `'Microsoft YaHei'` 改为 `'PingFang SC'`
- **Linux** — `sudo apt install fonts-wqy-microhei`

**Q: `fundamental` 策略返回 None**

需传入 `stock_info` 参数：

```python
stock_list = sina.get_stock_list()
result = sina.should_buy('sh600000', strategy='fundamental', stock_info=stock_list[0])
```

---

## 免责声明

本工具仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。评估结果基于历史数据和技术指标，无法预测未来走势。