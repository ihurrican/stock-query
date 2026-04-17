# -*- coding: utf-8 -*-
"""
stock-service.py — A 股数据服务与多策略买入评估工具

功能概述：
    1. 通过新浪财经 API 获取沪深 A 股的实时行情列表
    2. 获取指定股票的历史 K 线数据（支持分钟级 / 日 / 周 / 月线）
    3. 绘制 K 线图（含成交量，使用 mplfinance）
    4. 基于三种策略（技术面 / 基本面 / 趋势跟踪）和三档风险偏好（保守 / 稳健 / 激进）
       对股票进行买入评估打分

数据来源：
    新浪财经 vip 行情接口
    https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/

依赖：
    requests, pandas, numpy, mplfinance, matplotlib

用法：
    # 作为模块导入
    from stock_service import StockSinaService
    sina = StockSinaService()
    result = sina.should_buy('sh600000', strategy='all', risk='balanced', stock_info=item)

    # 直接运行：扫描全部 A 股，输出推荐购买的股票（按得分降序）
    python stock-service.py
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import concurrent.futures
import json
import time
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
import numpy as np
import sys
import io

# 强制将标准输出编码设为 UTF-8，避免 Windows 控制台输出中文时报编码错误
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


class StockSinaService:
    """
    新浪财经 A 股数据服务类。

    提供以下核心能力：
        - get_all_stock_list_count : 获取市场股票总数
        - get_stock_list           : 获取全量股票列表及最新行情（并发拉取）
        - get_stock_history_data   : 按 symbol 获取历史 K 线
        - get_stock_history_data_bycode : 按纯数字代码获取 K 线（自动匹配交易所）
        - get_stock_kline          : 获取 K 线数据并绘制图表
        - should_buy               : 多策略买入评估入口方法

    初始化时会自动创建带连接池和自动重试机制的 requests.Session，
    所有 HTTP 请求共享同一 TCP 连接池，大幅减少批量请求时的连接开销。
    """

    # 新浪财经行情 API 基地址
    base_url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'

    def __init__(self):
        """
        初始化 HTTP Session。

        配置说明：
            - User-Agent  : 模拟 Chrome 浏览器，避免被服务端拒绝
            - referer     : 设置来源页，部分接口会校验 referer
            - Retry       : 遇到 5xx 错误自动重试 3 次，重试间隔按 0.3s 指数退避
            - HTTPAdapter : 连接池大小 20，与线程池 max_workers=20 匹配
        """
        self.session = requests.Session()
        self.session.headers.update({
            'authority': 'vip.stock.finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
            'referer': 'https://vip.stock.finance.sina.com.cn/mkt/'
        })

        # 自动重试策略：总计重试 3 次，重试间隔 = 0.3 * (2 ^ 重试次数)
        # 仅对 500/502/503/504 状态码触发重试
        retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])

        # 连接池适配器：pool_connections=20 为同时保持的 TCP 连接数，
        # pool_maxsize=20 为每个主机的最大连接数
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    def http_get(self, url):
        """
        发送 HTTP GET 请求并返回响应文本。

        Args:
            url (str): 请求地址

        Returns:
            str: 响应体文本。请求失败时返回空字符串 ""。

        超时配置：
            - 连接超时 5 秒：建立 TCP 连接的最大等待时间
            - 读取超时 15 秒：等待服务端返回数据的最大时间
        """
        try:
            response = self.session.get(url, timeout=(5, 15))
            return response.text
        except requests.RequestException as e:
            print(f"Error fetching data from {url}: {e}")
            return ""

    # ==================== 股票列表相关 ====================

    def get_all_stock_list_count(self, node='hs_a'):
        """
        获取指定市场的股票总数。

        通过调用新浪 Market_Center.getHQNodeStockCount 接口，
        返回该 node 下的股票数量，用于后续分页拉取时计算总页数。

        Args:
            node (str): 市场节点代码，可选值：
                - 'hs_a'  : 沪深 A 股（默认）
                - 'dpzs'  : 大盘指数

        Returns:
            int: 股票总数。接口异常或解析失败时返回 0。
        """
        url = f"{self.base_url}Market_Center.getHQNodeStockCount?node={node}"
        try:
            # 接口返回纯数字字符串（带双引号），如 "5386"，需去除引号后转 int
            total_count = int(self.http_get(url).replace('"', ''))
        except Exception as e:
            total_count = 0
        print(f"总股票数量: {total_count}")
        return total_count

    def get_stock_list(self, node='hs_a'):
        """
        获取全量股票列表及最新行情数据。

        工作流程：
            1. 调用 get_all_stock_list_count 获取股票总数
            2. 按每页 80 条计算总页数，构建所有分页 URL
            3. 使用 20 线程并发拉取所有页面
            4. 合并解析结果为完整的股票列表

        Args:
            node (str): 市场节点代码，可选值：
                - 'hs_a'  : 沪深 A 股（默认）
                - 'dpzs'  : 大盘指数

        Returns:
            list[dict]: 股票行情列表，每个字典包含以下字段：
                - symbol          (str)  : 带交易所前缀的代码，如 "sh600000"、"sz000001"
                - code            (str)  : 纯数字代码，如 "600000"
                - name            (str)  : 股票名称，如 "浦发银行"
                - trade           (str)  : 当前价格（元）
                - pricechange     (str)  : 涨跌额（元）
                - changepercent   (str)  : 涨跌幅（%）
                - buy             (str)  : 买一价
                - sell            (str)  : 卖一价
                - settlement      (str)  : 昨收价
                - open            (str)  : 今日开盘价
                - high            (str)  : 今日最高价
                - low             (str)  : 今日最低价
                - volume          (str)  : 成交量（股）
                - amount          (str)  : 成交金额（元）
                - turnoverratio   (str)  : 换手率（%）
                - per             (str)  : 市盈率（PE）
                - pb              (str)  : 市净率（PB）
                - mktcap          (str)  : 总市值（万元）
                - nmc             (str)  : 流通市值（万元）
                - ticktime        (str)  : 最后更新时间
        """
        start_time = time.time()

        # 第一步：获取股票总数
        total_count = self.get_all_stock_list_count(node)

        # 第二步：计算分页参数
        # 每页 80 条是新浪接口的推荐值，过大可能被限流
        page_size = 80
        total_pages = (total_count // page_size) + 1

        # 第三步：构建所有分页的请求 URL
        # sort=open 按开盘价排序（接口要求必须指定排序字段）
        urls = []
        for page in range(1, total_pages + 1):
            url = (f"{self.base_url}Market_Center.getHQNodeData"
                   f"?page={page}&num={page_size}&sort=open&node={node}")
            urls.append(url)

        # 第四步：20 线程并发拉取所有页面
        stock_list = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_url = {executor.submit(self.http_get, url): url for url in urls}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    data = future.result()
                    stock_list.extend(json.loads(data))
                except Exception as exc:
                    print(f'{url} generated an exception: {exc}')

        # 输出统计信息
        print(f"实际获取股票数量: {len(stock_list)}")
        end_time = time.time()
        print(f"耗时: {end_time - start_time:.2f} 秒")
        return stock_list

    # ==================== 历史 K 线数据 ====================

    def get_stock_history_data(self, symbol, scale=5, datalen=1024):
        """
        根据股票 symbol 获取历史 K 线数据。

        调用新浪 CN_MarketData.getKLineData 接口，返回指定周期的 OHLCV 数据。
        接口同时返回均价和均量（ma 参数控制）。

        Args:
            symbol (str): 带交易所前缀的股票代码，如 "sh600000"（上海）、"sz000001"（深圳）
            scale  (int): K 线周期（分钟），可选值：
                - 1    : 1 分钟线
                - 5    : 5 分钟线（默认）
                - 15   : 15 分钟线
                - 30   : 30 分钟线
                - 60   : 60 分钟线
            datalen (int): 返回的数据条数，范围 1~1024，默认 1024

        Returns:
            list[dict]: K 线数据列表，每个字典包含：
                - day         (str)   : 时间，如 "2026-04-10 11:30:00"
                - open        (str)   : 开盘价
                - high        (str)   : 最高价
                - low         (str)   : 最低价
                - close       (str)   : 收盘价
                - volume      (str)   : 成交量（股）
                - ma_price{N} (float) : N 周期均价（N=scale）
                - ma_volume{N}(int)   : N 周期均量
            无数据时返回空列表 []。
        """
        url = (f"{self.base_url}CN_MarketData.getKLineData"
               f"?symbol={symbol}&scale={scale}&ma={scale}&datalen={datalen}")
        data = self.http_get(url)
        if not data or data == 'null':
            print(f"No data returned for {symbol} with scale {scale}")
            return []
        return json.loads(data)

    def get_stock_history_data_bycode(self, code, scale=5, datalen=1024):
        """
        根据纯数字股票代码获取历史 K 线数据，自动匹配交易所。

        由于纯数字代码不包含交易所信息，本方法会并发请求上海（sh）、
        深圳（sz）、北交所（bj）三个交易所，返回第一个有效的结果。
        相比顺序请求，最坏情况从等待 3 次超时降为 1 次。

        Args:
            code    (str): 纯数字股票代码，如 "600000"
            scale   (int): K 线周期（分钟），可选值：
                - 5    : 5 分钟线（默认）
                - 15   : 15 分钟线
                - 30   : 30 分钟线
                - 60   : 60 分钟线
                - 240  : 日线（4 小时 = 一个交易日）
                - 1200 : 周线
                - 7200 : 月线
            datalen (int): 返回的数据条数，范围 1~1024，默认 1024

        Returns:
            list[dict]: K 线数据列表（格式同 get_stock_history_data）。
            所有交易所都无数据时返回空列表 []。
        """
        # 构建三个交易所的请求 URL
        # ma=no 表示不返回均线数据（节省带宽，此接口主要用于获取原始 OHLCV）
        urls = [
            f"{self.base_url}CN_MarketData.getKLineData"
            f"?symbol={prefix}{code}&scale={scale}&ma=no&datalen={datalen}"
            for prefix in ('sh', 'sz', 'bj')
        ]

        # 3 线程并发请求，哪个先返回有效数据就用哪个
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(self.http_get, url): url for url in urls}
            for future in concurrent.futures.as_completed(futures):
                try:
                    raw = future.result()
                    if raw and raw != 'null':
                        return json.loads(raw)
                except Exception:
                    continue
        return []

    # ==================== K 线图绘制 ====================

    def get_stock_kline(self, symbol, scale=5, datalen=1024):
        """
        获取股票历史 K 线数据并绘制带成交量的 K 线图。

        使用 mplfinance 库绘制标准金融 K 线图，包含：
            - 上半部分：蜡烛图（红涨绿跌）
            - 下半部分：成交量柱状图

        注意：此方法会弹出 matplotlib 图形窗口，调用 plt.show() 会阻塞直到窗口关闭。

        Args:
            symbol  (str): 带交易所前缀的股票代码，如 "sh600000"
            scale   (int): K 线周期（分钟），默认 5 分钟
            datalen (int): 返回的数据条数，默认 1024
        """
        data = self.get_stock_history_data(symbol, scale, datalen)

        # 转换为 pandas DataFrame
        df = pd.DataFrame(data)

        # 将字符串类型的价格和成交量转换为数值类型
        df['day'] = pd.to_datetime(df['day'])
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(int)

        # mplfinance 要求以日期为索引
        df.set_index('day', inplace=True)
        df.sort_index(inplace=True)

        # 自定义样式：使用"微软雅黑"字体解决中文乱码问题
        custom_style = mpf.make_mpf_style(
            base_mpf_style='yahoo',
            rc={'font.family': 'Microsoft YaHei'}
        )
        mpf.plot(df,
                 type='candle',            # 蜡烛图类型
                 volume=True,              # 显示成交量
                 style=custom_style,       # 应用自定义样式
                 title='K线图',
                 ylabel='价格',
                 ylabel_lower='成交量',
                 figsize=(12, 8))

        plt.show()

    # ==================== 评分子策略 ====================

    def _score_technical(self, df, current_price, risk='balanced'):
        """
        技术面评分：基于 K 线价量关系的短中期分析。

        评估维度（7 项）：
            1. MA20 位置   — 价格与 20 日均线的相对位置
            2. MA60 位置   — 价格与 60 日均线的相对位置
            3. 近5日涨跌幅 — 短期价格动量
            4. 成交量      — 近 5 日均量 vs 前 20 日均量的比值
            5. 支撑/阻力   — 当前价格与 20 日高低点的距离
            6. ATR 波动率  — 14 日真实波幅占价格的百分比
            7. 突破迹象    — 收盘价是否突破前 5 日最高价

        风险等级对阈值的影响：
            - conservative（保守）：要求更严格的条件（如均线必须向上、放量倍数更高），
              单项得分上限较低，减少误判
            - balanced（稳健）   ：默认阈值，与经典技术分析教科书一致
            - aggressive（激进） ：放宽条件（如接近均线即可得分、低放量也计分），
              单项得分上限较高，捕捉更多机会

        Args:
            df            (DataFrame): 日线 K 线数据，需包含 open/high/low/close/volume 列
            current_price (float)    : 当前价格
            risk          (str)      : 风险偏好，'conservative' | 'balanced' | 'aggressive'

        Returns:
            tuple[int, list[str]]: (总得分, 评分理由列表)
        """
        score = 0
        reasons = []

        # ---------- 计算均线 ----------
        # MA20 = 20 日收盘价简单移动平均，反映中期趋势
        # MA60 = 60 日收盘价简单移动平均，反映长期趋势
        df['MA20'] = df['close'].rolling(window=20).mean()
        df['MA60'] = df['close'].rolling(window=60).mean()
        last = df.iloc[-1]  # 最新一条 K 线

        # ---------- 1. MA20 位置 ----------
        # 保守：价格必须站上 MA20 且 MA20 本身在上升（趋势确认）
        # 稳健：价格站上 MA20 即可
        # 激进：价格只要在 MA20 的 97% 以上就给分（容许小幅回调）
        if risk == 'conservative':
            ma20_rising = len(df) >= 2 and last['MA20'] > df['MA20'].iloc[-2]
            if last['close'] > last['MA20'] and ma20_rising:
                score += 10
                reasons.append("价格站上MA20且均线向上 (+10)")
            elif last['close'] > last['MA20'] * 0.98:
                score += 3
                reasons.append("价格接近MA20 (+3)")
            else:
                reasons.append("价格低于MA20 (0)")
        elif risk == 'aggressive':
            if last['close'] > last['MA20'] * 0.97:
                score += 15
                reasons.append("价格接近或站上MA20 (+15)")
            else:
                reasons.append("价格远低于MA20 (0)")
        else:  # balanced
            if last['close'] > last['MA20']:
                score += 15
                reasons.append("价格在20日均线之上 (+15)")
            elif last['close'] > last['MA20'] * 0.98:
                score += 5
                reasons.append("价格接近20日均线 (+5)")
            else:
                reasons.append("价格低于20日均线 (0)")

        # ---------- 2. MA60 位置 ----------
        # 逻辑与 MA20 类似，但 MA60 反映更长周期趋势
        # 数据不足 60 日时 MA60 为 NaN，跳过该维度
        if not pd.isna(last['MA60']):
            if risk == 'conservative':
                ma60_rising = len(df) >= 2 and last['MA60'] > df['MA60'].iloc[-2]
                if last['close'] > last['MA60'] and ma60_rising:
                    score += 10
                    reasons.append("价格站上MA60且均线向上 (+10)")
                else:
                    reasons.append("MA60条件不满足 (0)")
            else:  # balanced & aggressive
                if last['close'] > last['MA60']:
                    score += 15
                    reasons.append("价格在60日均线之上 (+15)")
                else:
                    reasons.append("价格低于60日均线 (0)")
        else:
            reasons.append("60日均线数据不足 (0)")

        # ---------- 3. 近 5 日涨跌幅 ----------
        # 计算最近 5 个交易日的累计收益率，衡量短期价格动量
        # 需要至少 6 条数据（当前 + 前 5 日）
        if len(df) >= 6:
            recent_ret = (df['close'].iloc[-1] / df['close'].iloc[-6] - 1) * 100
            if risk == 'conservative':
                # 保守：涨幅 >5% 才给满分，跌幅 >3% 扣分
                if recent_ret > 5:
                    score += 8
                    reasons.append(f"近5日上涨 {recent_ret:.1f}% (+8)")
                elif recent_ret > 1:
                    score += 4
                    reasons.append(f"近5日微涨 {recent_ret:.1f}% (+4)")
                elif recent_ret > -3:
                    reasons.append(f"近5日小跌 {recent_ret:.1f}% (0)")
                else:
                    score -= 8
                    reasons.append(f"近5日大跌 {recent_ret:.1f}% (-8)")
            elif risk == 'aggressive':
                # 激进：涨幅 >1% 即满分，更关注趋势启动信号
                if recent_ret > 1:
                    score += 12
                    reasons.append(f"近5日上涨 {recent_ret:.1f}% (+12)")
                elif recent_ret > -2:
                    score += 6
                    reasons.append(f"近5日微跌 {recent_ret:.1f}% (+6)")
                else:
                    reasons.append(f"近5日下跌 {recent_ret:.1f}% (0)")
            else:  # balanced
                if recent_ret > 3:
                    score += 10
                    reasons.append(f"近5日上涨 {recent_ret:.1f}% (+10)")
                elif recent_ret > 0:
                    score += 5
                    reasons.append(f"近5日微涨 {recent_ret:.1f}% (+5)")
                elif recent_ret > -5:
                    reasons.append(f"近5日下跌 {recent_ret:.1f}% (0)")
                else:
                    score -= 5
                    reasons.append(f"近5日大跌 {recent_ret:.1f}% (-5)")

        # ---------- 4. 成交量变化 ----------
        # 对比近 5 日均量与前 20 日均量，判断是否有资金介入
        # 放量上涨通常是主力进场的信号
        if len(df) >= 25:
            vol_5 = df['volume'].iloc[-5:].mean()      # 近 5 日平均成交量
            vol_20 = df['volume'].iloc[-25:-5].mean()   # 前 20 日平均成交量
            if vol_20 > 0:
                vol_ratio = vol_5 / vol_20  # 量比
                if risk == 'conservative':
                    # 保守：量比 >2 倍才视为有效放量
                    if vol_ratio > 2.0:
                        score += 8
                        reasons.append(f"近期显著放量 {vol_ratio:.2f}倍 (+8)")
                    elif vol_ratio > 1.5:
                        score += 4
                        reasons.append(f"近期温和放量 {vol_ratio:.2f}倍 (+4)")
                    else:
                        reasons.append(f"成交量无明显变化 (0)")
                elif risk == 'aggressive':
                    # 激进：量比 >1.2 倍即视为放量
                    if vol_ratio > 1.2:
                        score += 12
                        reasons.append(f"近期放量 {vol_ratio:.2f}倍 (+12)")
                    elif vol_ratio > 0.8:
                        score += 6
                        reasons.append(f"成交量正常 {vol_ratio:.2f}倍 (+6)")
                    else:
                        reasons.append(f"成交量萎缩 (0)")
                else:  # balanced
                    if vol_ratio > 1.5:
                        score += 10
                        reasons.append(f"近期放量 {vol_ratio:.2f}倍 (+10)")
                    elif vol_ratio > 1.2:
                        score += 5
                        reasons.append(f"近期温和放量 {vol_ratio:.2f}倍 (+5)")
                    else:
                        reasons.append(f"成交量无明显变化 (0)")

        # ---------- 5. 支撑/阻力位判断 ----------
        # 当前价格接近 20 日最低价时，可能获得支撑（逢低买入机会）
        # 接近 20 日最高价时，可能遇到阻力（追高风险）
        high_20 = df['high'].iloc[-20:].max()   # 20 日内最高价
        low_20 = df['low'].iloc[-20:].min()     # 20 日内最低价
        if risk == 'conservative':
            # 保守：价格必须在低点 1% 以内才算有效支撑
            if current_price <= low_20 * 1.01:
                score += 8
                reasons.append("价格紧贴20日低点，强支撑 (+8)")
            elif current_price >= high_20 * 0.98:
                reasons.append("价格接近20日高点，注意阻力 (0)")
        elif risk == 'aggressive':
            # 激进：低点 5% 以内都算支撑区域，接近高点视为突破前兆
            if current_price <= low_20 * 1.05:
                score += 12
                reasons.append("价格接近20日低点区域 (+12)")
            elif current_price >= high_20 * 0.98:
                score += 5
                reasons.append("价格接近突破高点 (+5)")
        else:  # balanced
            if current_price <= low_20 * 1.02:
                score += 10
                reasons.append("价格接近20日低点，可能支撑 (+10)")
            elif current_price >= high_20 * 0.98:
                reasons.append("价格接近20日高点，注意阻力 (0)")

        # ---------- 6. ATR 波动率 ----------
        # ATR (Average True Range) 衡量价格波动幅度
        # True Range = max(当日高-低, |当日高-昨收|, |当日低-昨收|)
        # ATR% = ATR / 当前价格 × 100，值越小说明价格越稳定
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.abs(df['high'] - df['close'].shift(1)),
            np.abs(df['low'] - df['close'].shift(1))
        )
        atr = df['tr'].rolling(14).mean().iloc[-1]     # 14 日平均真实波幅
        atr_pct = atr / current_price * 100             # ATR 占价格的百分比
        if risk == 'conservative':
            # 保守投资者偏好低波动，ATR% >3% 会扣分
            if atr_pct < 2:
                score += 10
                reasons.append(f"波动率很低 {atr_pct:.1f}% (+10)")
            elif atr_pct < 3:
                score += 5
                reasons.append(f"波动率较低 {atr_pct:.1f}% (+5)")
            else:
                score -= 3
                reasons.append(f"波动率偏高 {atr_pct:.1f}% (-3)")
        elif risk == 'aggressive':
            # 激进投资者对波动容忍度高，只要 <5% 就不扣分
            if atr_pct < 5:
                score += 5
                reasons.append(f"波动率可控 {atr_pct:.1f}% (+5)")
            else:
                reasons.append(f"波动率较高 {atr_pct:.1f}% (0)")
        else:  # balanced
            if atr_pct < 3:
                score += 10
                reasons.append(f"波动率较低 {atr_pct:.1f}% (+10)")
            elif atr_pct < 5:
                score += 5
                reasons.append(f"波动率适中 {atr_pct:.1f}% (+5)")
            else:
                reasons.append(f"波动率较高 {atr_pct:.1f}% (0)")

        # ---------- 7. 突破迹象 ----------
        # 判断最新收盘价是否突破前 5 日的最高价，突破意味着上行动能增强
        if len(df) >= 6:
            prev_high = df['high'].iloc[-6:-1].max()    # 前 5 日最高价
            if risk == 'conservative':
                # 保守：突破的同时必须伴随放量（量比 >1.3），避免假突破
                vol_5 = df['volume'].iloc[-5:].mean() if len(df) >= 25 else 0
                vol_20 = df['volume'].iloc[-25:-5].mean() if len(df) >= 25 else 1
                vol_up = vol_20 > 0 and vol_5 / vol_20 > 1.3
                if last['close'] > prev_high and vol_up:
                    score += 12
                    reasons.append("放量突破前5日高点 (+12)")
            elif risk == 'aggressive':
                # 激进：突破即加分，接近突破（99% 以上）也给部分分数
                if last['close'] > prev_high:
                    score += 15
                    reasons.append("突破前5日高点 (+15)")
                elif last['close'] > prev_high * 0.99:
                    score += 8
                    reasons.append("接近突破前5日高点 (+8)")
            else:  # balanced
                if last['close'] > prev_high:
                    score += 15
                    reasons.append("今日突破前5日高点 (+15)")

        return score, reasons

    def _score_fundamental(self, stock_info, risk='balanced'):
        """
        基本面评分：基于估值和市值的价值分析。

        评估维度（4 项）：
            1. PE 市盈率  — 股价 / 每股收益，衡量盈利能力估值
            2. PB 市净率  — 股价 / 每股净资产，衡量资产估值
            3. 市值规模   — 总市值大小，大盘股通常更稳定
            4. 流通占比   — 流通市值 / 总市值，占比越高流动性越好

        数据来源：
            stock_info 字典来自 get_stock_list() 返回的行情数据，
            其中 per=市盈率、pb=市净率、mktcap=总市值（万元）、nmc=流通市值（万元）。

        风险等级对阈值的影响：
            - conservative（保守）：只认可低 PE/PB 的价值股和大盘蓝筹
            - balanced（稳健）    ：接受合理估值的中盘股
            - aggressive（激进）  ：对高 PE/PB 的成长股也给分

        Args:
            stock_info (dict|None): get_stock_list 返回的单只股票行情字典
            risk       (str)      : 风险偏好

        Returns:
            tuple[int|None, list[str]]: (总得分, 评分理由列表)。
            stock_info 为空时返回 (None, [])。
        """
        if not stock_info:
            return None, []

        score = 0
        reasons = []

        # 从行情字典中提取基本面数据，缺失时默认为 0
        pe = float(stock_info.get('per', 0) or 0)         # 市盈率
        pb = float(stock_info.get('pb', 0) or 0)          # 市净率
        mktcap = float(stock_info.get('mktcap', 0) or 0)  # 总市值（万元）
        nmc = float(stock_info.get('nmc', 0) or 0)        # 流通市值（万元）

        # ---------- 1. PE 市盈率 ----------
        # PE <= 0 通常意味着亏损（负 PE）或数据缺失
        # 低 PE 代表估值便宜，但也可能是业绩下滑导致的"价值陷阱"
        if risk == 'conservative':
            if 0 < pe <= 15:
                score += 25
                reasons.append(f"PE={pe:.1f}，估值很低 (+25)")
            elif 0 < pe <= 25:
                score += 12
                reasons.append(f"PE={pe:.1f}，估值合理 (+12)")
            elif pe <= 0:
                reasons.append(f"PE={pe:.1f}，亏损或无数据 (0)")
            else:
                reasons.append(f"PE={pe:.1f}，估值偏高 (0)")
        elif risk == 'aggressive':
            # 激进策略对高 PE 的成长股更宽容（如科技、医药板块常有高 PE）
            if 0 < pe <= 50:
                score += 15
                reasons.append(f"PE={pe:.1f}，估值可接受 (+15)")
            elif 0 < pe <= 100:
                score += 8
                reasons.append(f"PE={pe:.1f}，估值偏高但有成长预期 (+8)")
            elif pe <= 0:
                reasons.append(f"PE={pe:.1f}，亏损或无数据 (0)")
            else:
                reasons.append(f"PE={pe:.1f}，估值过高 (0)")
        else:  # balanced
            if 0 < pe <= 25:
                score += 20
                reasons.append(f"PE={pe:.1f}，估值合理 (+20)")
            elif 0 < pe <= 40:
                score += 10
                reasons.append(f"PE={pe:.1f}，估值适中 (+10)")
            elif pe <= 0:
                reasons.append(f"PE={pe:.1f}，亏损或无数据 (0)")
            else:
                reasons.append(f"PE={pe:.1f}，估值偏高 (0)")

        # ---------- 2. PB 市净率 ----------
        # PB < 1 为"破净"，即股价低于每股净资产，理论上有安全边际
        # 但也可能是企业资产质量差导致
        if risk == 'conservative':
            if 0 < pb <= 1.5:
                score += 25
                reasons.append(f"PB={pb:.2f}，破净或接近净资产 (+25)")
            elif 0 < pb <= 3:
                score += 12
                reasons.append(f"PB={pb:.2f}，市净率合理 (+12)")
            else:
                reasons.append(f"PB={pb:.2f}，市净率偏高 (0)")
        elif risk == 'aggressive':
            if 0 < pb <= 5:
                score += 15
                reasons.append(f"PB={pb:.2f}，市净率可接受 (+15)")
            elif 0 < pb <= 10:
                score += 8
                reasons.append(f"PB={pb:.2f}，市净率偏高 (+8)")
            else:
                reasons.append(f"PB={pb:.2f}，市净率过高 (0)")
        else:  # balanced
            if 0 < pb <= 3:
                score += 20
                reasons.append(f"PB={pb:.2f}，市净率合理 (+20)")
            elif 0 < pb <= 5:
                score += 10
                reasons.append(f"PB={pb:.2f}，市净率适中 (+10)")
            else:
                reasons.append(f"PB={pb:.2f}，市净率偏高 (0)")

        # ---------- 3. 市值规模 ----------
        # mktcap 单位为万元，除以 10000 转换为亿元
        # 大盘股（>500 亿）通常更稳定，小盘股波动大但弹性好
        mktcap_yi = mktcap / 10000
        if risk == 'conservative':
            if mktcap_yi >= 500:
                score += 20
                reasons.append(f"大盘股 {mktcap_yi:.0f}亿，稳定性强 (+20)")
            elif mktcap_yi >= 200:
                score += 10
                reasons.append(f"中盘股 {mktcap_yi:.0f}亿 (+10)")
            else:
                reasons.append(f"小盘股 {mktcap_yi:.0f}亿，风险较大 (0)")
        elif risk == 'aggressive':
            # 激进策略不限市值，小盘股反而更可能有超额收益
            if mktcap_yi > 0:
                score += 20
                reasons.append(f"市值 {mktcap_yi:.0f}亿 (+20)")
        else:  # balanced
            if mktcap_yi >= 100:
                score += 20
                reasons.append(f"中大盘股 {mktcap_yi:.0f}亿 (+20)")
            elif mktcap_yi >= 30:
                score += 10
                reasons.append(f"小盘股 {mktcap_yi:.0f}亿 (+10)")
            else:
                reasons.append(f"微盘股 {mktcap_yi:.0f}亿，流动性风险 (0)")

        # ---------- 4. 流通市值占比 ----------
        # 流通占比 = 流通市值 / 总市值
        # 占比越高说明限售股越少，市场流动性越好，大单不容易被"锁仓"影响
        if mktcap > 0 and nmc > 0:
            ratio = nmc / mktcap
            if risk == 'conservative':
                if ratio >= 0.7:
                    score += 15
                    reasons.append(f"流通占比 {ratio:.0%}，流动性好 (+15)")
                elif ratio >= 0.5:
                    score += 8
                    reasons.append(f"流通占比 {ratio:.0%} (+8)")
                else:
                    reasons.append(f"流通占比 {ratio:.0%}，流动性差 (0)")
            elif risk == 'aggressive':
                # 激进策略不太在乎流通占比
                if nmc > 0:
                    score += 15
                    reasons.append(f"流通占比 {ratio:.0%} (+15)")
            else:  # balanced
                if ratio >= 0.5:
                    score += 15
                    reasons.append(f"流通占比 {ratio:.0%}，流动性良好 (+15)")
                elif ratio >= 0.3:
                    score += 8
                    reasons.append(f"流通占比 {ratio:.0%} (+8)")
                else:
                    reasons.append(f"流通占比 {ratio:.0%}，流动性偏低 (0)")
        else:
            reasons.append("流通市值数据不足 (0)")

        return score, reasons

    def _score_trend(self, df, risk='balanced'):
        """
        趋势跟踪评分：基于经典技术指标的趋势强度判断。

        评估维度（4 项）：
            1. MACD       — 快慢均线差值的趋势信号（金叉/死叉、柱状线动能）
            2. 布林带     — 价格在统计通道中的位置（上轨/中轨/下轨）
            3. RSI(14)    — 相对强弱指标，衡量超买超卖程度
            4. 均线多头排列 — 短中长期均线的排列顺序

        指标计算说明：
            - MACD: DIF = EMA12 - EMA26，DEA = EMA9(DIF)，柱状线 = (DIF-DEA)*2
            - 布林带: 中轨 = MA20，上轨 = MA20 + 2σ，下轨 = MA20 - 2σ
            - RSI: RS = 14日平均涨幅 / 14日平均跌幅，RSI = 100 - 100/(1+RS)
            - 均线: MA5 / MA10 / MA20 / MA60

        风险等级对判断标准的影响：
            - conservative（保守）：要求趋势确认信号更强（如 MACD 柱状线必须持续放大、
              RSI 在超卖区回升、布林带从下轨反弹）
            - balanced（稳健）    ：标准教科书级别判断
            - aggressive（激进）  ：捕捉趋势萌芽信号（如 MACD 即将金叉、突破布林上轨、
              RSI 进入强势区间）

        Args:
            df   (DataFrame): 日线 K 线数据
            risk (str)      : 风险偏好

        Returns:
            tuple[int, list[str]]: (总得分, 评分理由列表)
        """
        score = 0
        reasons = []
        close = df['close']

        # ---------- 1. MACD ----------
        # EMA12（快线）对价格变化更敏感，EMA26（慢线）更平滑
        # DIF = 快线 - 慢线，DIF > 0 表示短期趋势强于长期
        # DEA = DIF 的 9 日指数平均，用于平滑 DIF
        # 金叉 = DIF 上穿 DEA，是经典买入信号
        # 柱状线 = (DIF - DEA) * 2，柱状线持续放大表示趋势加速
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd_hist = (dif - dea) * 2

        if risk == 'conservative':
            # 保守：不仅要金叉，还要柱状线连续 3 日放大（动能持续增强）
            if dif.iloc[-1] > dea.iloc[-1] and len(macd_hist) >= 3:
                hist_rising = all(
                    macd_hist.iloc[-i] > macd_hist.iloc[-i-1]
                    for i in range(1, min(4, len(macd_hist)))
                )
                if hist_rising:
                    score += 20
                    reasons.append("MACD金叉且柱状线连续放大 (+20)")
                else:
                    score += 8
                    reasons.append("MACD金叉但动能未持续放大 (+8)")
            else:
                reasons.append("MACD未形成金叉 (0)")
        elif risk == 'aggressive':
            # 激进：金叉给满分，DIF 即将上穿 DEA 也给部分分（提前埋伏）
            if dif.iloc[-1] > dea.iloc[-1]:
                score += 20
                reasons.append("MACD金叉 (+20)")
            elif (len(dif) >= 2
                  and dif.iloc[-1] > dif.iloc[-2]
                  and dif.iloc[-1] > dea.iloc[-1] * 0.95):
                score += 12
                reasons.append("DIF即将上穿DEA (+12)")
            else:
                reasons.append("MACD偏弱 (0)")
        else:  # balanced
            if dif.iloc[-1] > dea.iloc[-1]:
                score += 20
                reasons.append("MACD金叉 (+20)")
            else:
                reasons.append("MACD未形成金叉 (0)")

        # ---------- 2. 布林带 (Bollinger Bands) ----------
        # 中轨 = 20 日均线，上下轨 = 中轨 ± 2 倍标准差
        # 价格在上轨之上表示极度强势，在下轨之下表示极度弱势
        # 约 95% 的价格波动落在布林带内
        ma20 = close.rolling(window=20).mean()
        std20 = close.rolling(window=20).std()
        upper = ma20 + 2 * std20    # 上轨
        lower = ma20 - 2 * std20    # 下轨
        last_close = close.iloc[-1]

        if not pd.isna(ma20.iloc[-1]):
            if risk == 'conservative':
                # 保守：关注从下轨反弹的"均值回归"机会
                if (len(close) >= 2
                        and close.iloc[-2] <= lower.iloc[-2]
                        and last_close > lower.iloc[-1]):
                    score += 20
                    reasons.append("价格从布林下轨反弹 (+20)")
                elif last_close > lower.iloc[-1] and last_close <= ma20.iloc[-1]:
                    score += 10
                    reasons.append("价格在布林下轨与中轨之间 (+10)")
                else:
                    reasons.append("布林带位置不理想 (0)")
            elif risk == 'aggressive':
                # 激进：突破上轨视为强势延续信号（追涨策略）
                if last_close > upper.iloc[-1]:
                    score += 20
                    reasons.append("价格突破布林上轨，强势 (+20)")
                elif last_close > ma20.iloc[-1]:
                    score += 15
                    reasons.append("价格在布林中轨之上 (+15)")
                else:
                    score += 5
                    reasons.append("价格在布林中轨之下 (+5)")
            else:  # balanced
                if last_close > ma20.iloc[-1]:
                    score += 20
                    reasons.append("价格在布林中轨之上 (+20)")
                elif last_close > lower.iloc[-1]:
                    score += 10
                    reasons.append("价格在布林下轨之上 (+10)")
                else:
                    reasons.append("价格跌破布林下轨 (0)")
        else:
            reasons.append("布林带数据不足 (0)")

        # ---------- 3. RSI (Relative Strength Index) ----------
        # RSI 取值 0~100：
        #   >70 超买区（可能回调），<30 超卖区（可能反弹），40~60 中性
        # 计算方法：先求涨跌幅，再分别取涨幅和跌幅的 14 日均值
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(window=14).mean()    # 14 日平均涨幅
        loss = (-delta.clip(upper=0)).rolling(window=14).mean()  # 14 日平均跌幅
        rs = gain / loss.replace(0, np.nan)     # 避免除以 0
        rsi = 100 - (100 / (1 + rs))
        last_rsi = rsi.iloc[-1]

        if not pd.isna(last_rsi):
            if risk == 'conservative':
                # 保守：偏好超卖区回升（30~50），属于"低吸"策略
                if 30 <= last_rsi <= 50:
                    score += 20
                    reasons.append(f"RSI={last_rsi:.1f}，超卖区回升 (+20)")
                elif 50 < last_rsi <= 60:
                    score += 10
                    reasons.append(f"RSI={last_rsi:.1f}，中性偏强 (+10)")
                elif last_rsi < 30:
                    score += 5
                    reasons.append(f"RSI={last_rsi:.1f}，深度超卖 (+5)")
                else:
                    reasons.append(f"RSI={last_rsi:.1f}，偏超买 (0)")
            elif risk == 'aggressive':
                # 激进：偏好强势区间（50~80），属于"追强"策略
                if 50 <= last_rsi <= 80:
                    score += 20
                    reasons.append(f"RSI={last_rsi:.1f}，强势区间 (+20)")
                elif 40 <= last_rsi < 50:
                    score += 12
                    reasons.append(f"RSI={last_rsi:.1f}，即将转强 (+12)")
                elif last_rsi > 80:
                    score += 5
                    reasons.append(f"RSI={last_rsi:.1f}，超买但趋势强 (+5)")
                else:
                    reasons.append(f"RSI={last_rsi:.1f}，偏弱 (0)")
            else:  # balanced
                if 40 <= last_rsi <= 60:
                    score += 20
                    reasons.append(f"RSI={last_rsi:.1f}，中性健康区间 (+20)")
                elif 30 <= last_rsi < 40:
                    score += 12
                    reasons.append(f"RSI={last_rsi:.1f}，接近超卖 (+12)")
                elif 60 < last_rsi <= 70:
                    score += 10
                    reasons.append(f"RSI={last_rsi:.1f}，偏强 (+10)")
                else:
                    reasons.append(f"RSI={last_rsi:.1f}，极端区域 (0)")
        else:
            reasons.append("RSI数据不足 (0)")

        # ---------- 4. 均线多头排列 ----------
        # 多头排列 = 短期均线在上、长期均线在下 (MA5 > MA10 > MA20 > MA60)
        # 表明各周期趋势一致向上，是最强的看多信号
        df['MA5'] = close.rolling(window=5).mean()
        df['MA10'] = close.rolling(window=10).mean()
        df['MA20'] = close.rolling(window=20).mean()
        df['MA60'] = close.rolling(window=60).mean()
        last = df.iloc[-1]

        if risk == 'conservative':
            # 保守：要求完美 4 线多头排列（含 MA60）
            if (not pd.isna(last['MA60'])
                    and last['MA5'] > last['MA10'] > last['MA20'] > last['MA60']):
                score += 20
                reasons.append("MA5>MA10>MA20>MA60 完美多头排列 (+20)")
            elif last['MA5'] > last['MA10'] > last['MA20']:
                score += 8
                reasons.append("MA5>MA10>MA20 短中期多头 (+8)")
            else:
                reasons.append("均线未形成多头排列 (0)")
        elif risk == 'aggressive':
            # 激进：MA5 > MA10 即认为短期趋势向上
            if last['MA5'] > last['MA10']:
                score += 20
                reasons.append("MA5>MA10 短期多头 (+20)")
            else:
                reasons.append("短期均线空头 (0)")
        else:  # balanced
            if last['MA5'] > last['MA10'] > last['MA20']:
                score += 20
                reasons.append("MA5>MA10>MA20 多头排列 (+20)")
            elif last['MA5'] > last['MA10']:
                score += 10
                reasons.append("MA5>MA10 短期多头 (+10)")
            else:
                reasons.append("均线未形成多头排列 (0)")

        return score, reasons

    # ==================== 综合评估入口 ====================

    def should_buy(self, symbol, strategy='all', risk='balanced',
                   current_price=None, stock_info=None):
        """
        多策略买入评估入口方法。

        根据指定的 strategy 和 risk 对股票进行综合打分，返回评估结果。
        支持单独使用某一策略，也支持综合模式（加权合并所有策略）。

        策略说明：
            - 'technical'   : 技术面 — 基于 K 线价量关系（MA/成交量/ATR/突破）
            - 'fundamental' : 基本面 — 基于估值指标（PE/PB/市值/流通率）
            - 'trend'       : 趋势跟踪 — 基于技术指标（MACD/布林带/RSI/均线排列）
            - 'all'         : 综合模式 — 三种策略加权合并

        综合模式的权重分配：
            - 有 stock_info 时：技术面 40% + 基本面 30% + 趋势 30%
            - 无 stock_info 时：技术面 50% + 趋势 50%（自动跳过基本面）

        决策阈值根据风险等级调整：
            - conservative（保守）：推荐购买 >= 70，观望 >= 50
            - balanced（稳健）    ：推荐购买 >= 60，观望 >= 40
            - aggressive（激进）  ：推荐购买 >= 50，观望 >= 30

        Args:
            symbol        (str)       : 带交易所前缀的股票代码，如 "sh600000"
            strategy      (str)       : 评估策略，默认 'all'
            risk          (str)       : 风险偏好，默认 'balanced'
            current_price (float|None): 当前价格，为 None 时取最近一日收盘价
            stock_info    (dict|None) : get_stock_list 返回的行情字典，
                                        基本面策略需要此参数（含 per/pb/mktcap/nmc）

        Returns:
            dict|None: 评估结果字典，无数据时返回 None。字段包括：
                - symbol        (str)       : 股票代码
                - strategy      (str)       : 使用的策略
                - risk          (str)       : 风险等级
                - score         (float)     : 综合得分
                - decision      (str)       : 决策建议（"推荐购买" / "观望" / "不推荐"）
                - reasons       (list[str]) : 评分理由详情
                - current_price (float)     : 评估时的价格
        """
        # 获取日线级别的历史 K 线数据（scale=240 即日线，200 个交易日约 10 个月）
        data_list = self.get_stock_history_data(symbol, scale=240, datalen=200)
        if not data_list:
            print(f"无法获取 {symbol} 的历史数据")
            return None

        # 构建 DataFrame 并转换数据类型
        df = pd.DataFrame(data_list)
        df['day'] = pd.to_datetime(df['day'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        df.sort_values('day', inplace=True)
        df.reset_index(drop=True, inplace=True)

        # 默认使用最近一日收盘价作为当前价格
        if current_price is None:
            current_price = df['close'].iloc[-1]

        all_reasons = []

        # ---------- 根据策略模式调用对应的评分方法 ----------
        if strategy == 'technical':
            score, reasons = self._score_technical(df, current_price, risk)
            all_reasons = reasons

        elif strategy == 'fundamental':
            result = self._score_fundamental(stock_info, risk)
            if result[0] is None:
                print(f"{symbol} 缺少基本面数据(stock_info)，无法评估")
                return None
            score, reasons = result
            all_reasons = reasons

        elif strategy == 'trend':
            score, reasons = self._score_trend(df, risk)
            all_reasons = reasons

        else:
            # ---------- 综合模式：加权合并三种策略 ----------
            tech_score, tech_reasons = self._score_technical(df, current_price, risk)
            trend_score, trend_reasons = self._score_trend(df, risk)
            fund_result = self._score_fundamental(stock_info, risk)

            all_reasons.append("== 技术面 ==")
            all_reasons.extend(tech_reasons)
            all_reasons.append("== 趋势跟踪 ==")
            all_reasons.extend(trend_reasons)

            if fund_result[0] is not None:
                fund_score, fund_reasons = fund_result
                all_reasons.append("== 基本面 ==")
                all_reasons.extend(fund_reasons)
                # 各策略原始分归一化到百分制后加权
                # tech_max=85, fund_max=85, trend_max=80 为各策略的理论最高分
                tech_max, fund_max, trend_max = 85, 85, 80
                score = (tech_score / tech_max * 100 * 0.4 +
                         fund_score / fund_max * 100 * 0.3 +
                         trend_score / trend_max * 100 * 0.3)
            else:
                # 无基本面数据时，技术面和趋势各占 50%
                all_reasons.append("== 基本面 == (无数据，跳过)")
                tech_max, trend_max = 85, 80
                score = (tech_score / tech_max * 100 * 0.5 +
                         trend_score / trend_max * 100 * 0.5)
            score = round(score, 1)

        # ---------- 决策阈值按风险等级调整 ----------
        if risk == 'conservative':
            buy_threshold, watch_threshold = 70, 50
        elif risk == 'aggressive':
            buy_threshold, watch_threshold = 50, 30
        else:
            buy_threshold, watch_threshold = 60, 40

        if score >= buy_threshold:
            decision = "推荐购买"
        elif score >= watch_threshold:
            decision = "观望"
        else:
            decision = "不推荐"

        return {
            'symbol': symbol,
            'strategy': strategy,
            'risk': risk,
            'score': score,
            'decision': decision,
            'reasons': all_reasons,
            'current_price': current_price
        }
    
    def stock_suggestion(self, top=10, score=60, risk='balanced', strategy='all'):
        print(f"正在获取股票建议列表（top={top}，score>={score}，risk={risk}，strategy={strategy}）...")
        """
        获取股票建议列表（批量筛选推荐购买的股票）。

        本方法会：
        1. 并发获取全量 A 股行情列表
        2. 多线程并发评估所有股票的买入评分
        3. 按得分降序排列，返回前 top 个推荐

        为提高批量评估效率，内部使用单线程顺序执行评估
        （避免嵌套多线程导致的上下文切换开销）。

        Args:
            top      (int)  : 返回建议的股票数量，默认 10
            score    (int)  : 最低筛选分数，默认 60（稳健型推荐阈值）
            risk     (str)  : 风险偏好，默认 'balanced'
            strategy (str)  : 评分策略，默认 'all'

        Returns:
            list[dict]: 股票建议列表，按得分降序排列。每项包含：
                - symbol        (str)   : 股票代码
                - strategy     (str)   : 使用的策略
                - risk         (str)   : 风险等级
                - score        (float) : 综合得分
                - decision     (str)   : 决策建议
                - reasons      (list)  : 评分理由
                - current_price (float): 当前价格
        """
        print(f"正在获取全量 A 股列表...")
        stock_list = self.get_stock_list()

        if not stock_list:
            print("无法获取股票列表")
            return []

        print(f"正在评估 {len(stock_list)} 只股票（{strategy} 策略，{risk} 风险）...")

        suggestions = []
        count = 0
        total = len(stock_list)

        for item in stock_list:
            count += 1
            if count % 500 == 0:
                print(f"进度: {count}/{total}")

            try:
                result = self.should_buy(
                    item['symbol'],
                    strategy=strategy,
                    risk=risk,
                    stock_info=item
                )
                if result and result['score'] >= score:
                    suggestions.append(result)
            except Exception as exc:
                print(f"{item['symbol']} 评估异常: {exc}")

        sorted_suggestions = sorted(suggestions, key=lambda x: x['score'], reverse=True)
        print(f"评估完成，符合条件的股票: {len(suggestions)}")
        return sorted_suggestions[:top]

