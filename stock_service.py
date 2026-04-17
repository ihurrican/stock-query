# -*- coding: utf-8 -*-
import sys
import io
import argparse
from sina_service import StockSinaService

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

__version__ = '1.0.0'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='stock_service',
        description='基于新浪财经 API，提供沪深 A 股实时行情查询、历史 K 线获取、K 线图绘制，以及多策略买入评分系统。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例用法:
  %(prog)s --list                    获取全部A股行情列表
  %(prog)s --history sh600000          获取600000的日K线数据
  %(prog)s --kline sh600000           获取并绘制K线图
  %(prog)s --score sh600000           评估买入评分
  %(prog)s --code 600000            根据纯数字代码查询（自动匹配交易所）
  %(prog)s --suggest                批量筛选推荐股票
  %(prog)s --suggest --top 20          筛选前20只推荐股票

策略选项 (--score):
  technical   技术面分析 (MA/成交量/ATR/突破)
  fundamental 基本面分析 (PE/PB/市值/流通率)
  trend       趋势跟踪 (MACD/布林/RSI/均线)
  all         综合模式 (三种策略加权)

风险偏好 (--risk):
  conservative 保守型 (要求严格)
  balanced    稳健型 (默认)
  aggressive 激进型 (放宽带宽)
'''
    )

    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--list', action='store_true', help='获取沪深A股完整列表')
    group.add_argument('--count', action='store_true', help='获取股票总数')
    group.add_argument('--history', metavar='SYMBOL', help='获取历史K线 (如 sh600000)')
    group.add_argument('--kline', metavar='SYMBOL', help='获取并绘制K线图')
    group.add_argument('--score', metavar='SYMBOL', help='买入评估评分')
    group.add_argument('--code', metavar='CODE', help='根据纯数字代码查询 (自动匹配交易所)')
    group.add_argument('--suggest', action='store_true', help='获取推荐股票列表 (批量筛选)')

    parser.add_argument('--top', type=int, default=10, help='返回股票数量 (默认: 10)')
    parser.add_argument('--scale', type=int, default=240,
                        help='K线周期: 5/15/30/60分钟, 240日线, 1200周线, 7200月线 (默认: 240)')
    parser.add_argument('--datalen', type=int, default=1024,
                        help='返回数据条数, 范围1-1024 (默认: 1024)')
    parser.add_argument('--strategy', choices=['technical', 'fundamental', 'trend', 'all'],
                        default='all', help='评分策略 (默认: all)')
    parser.add_argument('--risk', choices=['conservative', 'balanced', 'aggressive'],
                        default='balanced', help='风险偏好 (默认: balanced)')


    return parser


def main():

    parser = build_parser()
    args = parser.parse_args()

    sina = StockSinaService()

    try:
        if args.list:
            stocks = sina.get_stock_list()
            print(f'\n共获取 {len(stocks)} 只股票')
            print(stocks)

        elif args.count:
            count = sina.get_all_stock_list_count()
            print(f'股票总数: {count}')

        elif args.history:
            data = sina.get_stock_history_data(args.history, args.scale, args.datalen)
            print(f'\n获取 {args.history} K线数据 {len(data)} 条')
            if data:
                print(f'最新: {data[-1]}')

        elif args.kline:
            print(f'正在绘制 {args.kline} K线图...')
            sina.get_stock_kline(args.kline, args.scale, args.datalen)

        elif args.score:
            result = sina.should_buy(args.score, strategy=args.strategy, risk=args.risk)
            if result:
                print(f'\n股票: {result["symbol"]}')
                print(f'策略: {result["strategy"]}  风险: {result["risk"]}')
                print(f'得分: {result["score"]:.1f}')
                print(f'决策: {result["decision"]}')
                print(f'当前价: {result["current_price"]}')
                if result.get('reasons'):
                    print('\n评分理由:')
                    for r in result['reasons']:
                        print(f'  - {r}')
            else:
                print(f'无法获取 {args.score} 的评估数据')

        elif args.code:
            data = sina.get_stock_history_data_bycode(args.code, args.scale, args.datalen)
            print(f'\n获取代码 {args.code} K线数据 {len(data)} 条')
            if data:
                print(f'最新: {data[-1]}')

        elif args.suggest:
            suggestions = sina.stock_suggestion(
                top=args.top,
                score=60 if args.risk == 'balanced' else (70 if args.risk == 'conservative' else 50),
                risk=args.risk,
                strategy=args.strategy
            )
            print(f'\n推荐股票 (共 {len(suggestions)} 只):')
            for i, s in enumerate(suggestions, 1):
                print(f'{i}. {s["symbol"]} 得分:{s["score"]:.1f} 决策:{s["decision"]} 当前价:{s["current_price"]}')

        else:
            parser.print_help()
    except Exception as e:
        print(f'错误: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()