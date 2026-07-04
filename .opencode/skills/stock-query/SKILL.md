---
name: stock-query
description: A股数据查询与多策略买入评估工具，基于新浪财经 API 提供实时行情、历史K线、K线图绘制、多策略买入评分和批量筛选功能
license: MIT
compatibility: opencode
metadata:
  language: python
  data_source: sina_finance
---

## 项目结构

- `sina_service.py` — 核心服务类 `StockSinaService`，封装所有 API 调用和评分逻辑
- `stock_service.py` — 命令行入口 `StockServiceCLI`，提供 CLI 参数解析和交互
- `main.py` — 快捷入口，默认执行 `--export`
- `requirements.txt` — Python 依赖
- `data.txt` — 股票代码列表（供导出使用）

## 环境要求

- Python >= 3.8
- 依赖: `requests`, `pandas`, `numpy`, `mplfinance`, `matplotlib`
- 网络: 需能访问 `vip.stock.finance.sina.com.cn`

安装: `pip install -r requirements.txt`

## 核心能力

### 1. 获取股票列表与实时行情
```python
from sina_service import StockSinaService
sina = StockSinaService()
stock_list = sina.get_stock_list()              # 沪深A股完整列表
dpzs_list = sina.get_stock_list(node='dpzs')    # 大盘指数
```
返回字段: symbol, code, name, trade, changepercent, volume, per, pb, mktcap 等。

### 2. 获取历史 K 线数据
```python
sina.get_stock_history_data('sh600000', scale=240, datalen=200)
sina.get_stock_history_data_bycode('600000', scale=240, datalen=200)
```
- scale: 1/5/15/30/60(分钟), 240(日线), 1200(周线), 7200(月线)
- 返回: day, open/high/low/close, volume, ma_price{5/10/20/60}, ma_volume{5/10/20}

### 3. 绘制 K 线图
```python
sina.get_stock_kline('sh600000', scale=60, datalen=200)
```
弹出 matplotlib 窗口，显示蜡烛图+成交量。

### 4. 多策略买入评估 (核心)
```python
result = sina.should_buy('sh600000', strategy='all', risk='balanced', stock_info=item)
```
返回: symbol, score, decision(推荐购买/观望/不推荐), reasons, current_price

#### 策略
| 策略 | 参数值 | 分析维度 |
|------|--------|----------|
| 技术面 | `technical` | MA均线、成交量、支撑阻力、ATR波动率、突破 |
| 基本面 | `fundamental` | PE市盈率、PB市净率、市值规模、流通占比 |
| 趋势跟踪 | `trend` | MACD、金叉死叉、RSI、均线多头排列 |
| 综合模式 | `all`（默认） | 以上三种策略加权合并 |

#### 风险等级
| 等级 | 参数值 | 推荐购买阈值 |
|------|--------|-------------|
| 保守 | `conservative` | >= 70 分 |
| 稳健 | `balanced` | >= 60 分 |
| 激进 | `aggressive` | >= 50 分 |

### 5. 批量筛选推荐股票
```python
suggestions = sina.stock_suggestion(top=10, score=60, risk='balanced')
```
并发获取全量A股，多线程评估，按得分降序返回。

### 6. 批量导出周K线到 Excel
```python
cli = StockServiceCLI()
cli._handle_export(weeks=55, datadir='data.txt')
```
每只股票一个 xlsx 文件，包含 day, open/high/low/close, volume, direction。

### 7. 批量获取多只股票历史数据
```python
sina.get_multi_stock_history_to_xlsx(symbols, scale=240, datalen=1024)
```
多线程并发拉取，每只股票一个 sheet，自动生成汇总 summary sheet。

## 命令行用法
```bash
python stock_service.py --list                   # 获取全部A股
python stock_service.py --count                  # 股票总数
python stock_service.py --history sh600000       # 历史K线
python stock_service.py --kline sh600000         # 绘制K线图
python stock_service.py --score sh600000         # 买入评分
python stock_service.py --code 600000            # 数字代码查询
python stock_service.py --suggest                # 批量推荐
python stock_service.py --suggest --top 20 --score 70 --risk conservative
python stock_service.py --export                 # 导出周K线Excel
```

## 注意事项
1. 需网络访问新浪财经 API，被限流时自动重试 3 次
2. 基本面策略必须传入 stock_info
3. 综合模式权重: 有信息时技术40%+基本面30%+趋势30%，无信息时技术50%+趋势50%
4. 新浪数据有 15 分钟延迟
5. 本工具仅供学习研究，不构成投资建议
