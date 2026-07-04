import os
import sys
import io
import argparse
from datetime import datetime
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np
from sina_service import StockSinaService

__version__ = '1.0.1'


class StockServiceCLI:
    """股票服务命令行接口"""
    
    def __init__(self):
        self._setup_stdout()
        self.sina = StockSinaService()  # 假设这个类已定义
        self._setup_exception_handler()
    
    def _setup_stdout(self):
        """安全设置标准输出编码"""
        try:
            # 检查是否已经是 UTF-8 编码
            if hasattr(sys.stdout, 'encoding') and sys.stdout.encoding == 'utf-8':
                return
            
            # 安全地重新包装 stdout
            if hasattr(sys.stdout, 'buffer') and sys.stdout.buffer:
                sys.stdout = io.TextIOWrapper(
                    sys.stdout.buffer, 
                    encoding='utf-8',
                    errors='replace'  # 处理无法编码的字符
                )
        except (AttributeError, ValueError, OSError):
            # 如果失败，保持原样，不做修改
            pass
    
    def _setup_exception_handler(self):
        """设置全局异常处理"""
        sys.excepthook = self._global_exception_handler
    
    @staticmethod
    def _global_exception_handler(exc_type, exc_value, exc_traceback):
        """全局异常处理器"""
        print(f'错误: {exc_value}', file=sys.stderr)
        sys.exit(1)
    
    @staticmethod
    def _validate_datalen(datalen: int) -> int:
        """验证数据长度参数"""
        return max(1, min(datalen, 1024))
    
    def _handle_list(self):
        """处理列表查询"""
        stocks = self.sina.get_stock_list()
        print(f'\n共获取 {len(stocks)} 只股票')
        print(stocks)
    
    def _handle_count(self):
        """处理总数查询"""
        count = self.sina.get_all_stock_list_count()
        print(f'股票总数: {count}')
    
    def _handle_history(self, symbol: str, scale: int, datalen: int):
        """处理历史数据查询"""
        data = self.sina.get_stock_history_data(symbol, scale, datalen)
        print(f'\n获取 {symbol} K线数据 {len(data)} 条')
        if data:
            latest = data[-1]
            print(f'最新: {latest}')
    
    def _handle_kline(self, symbol: str, scale: int, datalen: int):
        """处理K线图绘制"""
        print(f'正在绘制 {symbol} K线图...')
        self.sina.get_stock_kline(symbol, scale, datalen)
    
    def _handle_score(self, symbol: str, strategy: str, risk: str):
        """处理评分查询"""
        result = self.sina.should_buy(symbol, strategy=strategy, risk=risk)
        if not result:
            print(f'无法获取 {symbol} 的评估数据')
            return
        
        print(f'\n股票: {result["symbol"]}')
        print(f'策略: {result["strategy"]}  风险: {result["risk"]}')
        print(f'得分: {result["score"]:.1f}')
        print(f'决策: {result["decision"]}')
        print(f'当前价: {result["current_price"]}')
        
        if result.get('reasons'):
            print('\n评分理由:')
            for reason in result['reasons']:
                print(f'  - {reason}')
    
    def _handle_code(self, code: str, scale: int, datalen: int):
        """处理数字代码查询"""
        data = self.sina.get_stock_history_data_bycode(code, scale, datalen)
        print(f'\n获取代码 {code} K线数据 {len(data)} 条')
        if data:
            print(f'最新: {data[-1]}')
    
    def _get_score_threshold(self, risk: str) -> int:
        """根据风险偏好获取评分阈值"""
        thresholds = {
            'conservative': 70,
            'balanced': 60,
            'aggressive': 50
        }
        return thresholds.get(risk, 60)
    
    def _handle_suggest(self, top: int, risk: str, strategy: str):
        """处理股票推荐"""
        score_threshold = self._get_score_threshold(risk)
        suggestions = self.sina.stock_suggestion(
            top=top,
            score=score_threshold,
            risk=risk,
            strategy=strategy
        )
        
        if not suggestions:
            print('\n未找到符合条件的推荐股票')
            return
        
        print(f'\n推荐股票 (共 {len(suggestions)} 只):')
        for i, stock in enumerate(suggestions, 1):
            print(f'{i}. {stock["symbol"]} 得分:{stock["score"]:.1f} '
                  f'决策:{stock["decision"]} 当前价:{stock["current_price"]}')
    
    def _handle_export(self, weeks: int, datadir: str):
        codes = []
        with open(datadir, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    codes.append(line.split()[-1].strip())

        if not codes:
            print(f'{datadir} 中没有找到股票代码')
            return

        dir_name = datetime.now().strftime('%Y%m%d_%H%M%S')
        os.makedirs(dir_name, exist_ok=True)
        print(f'输出目录: {dir_name}')

        for code in codes:
            print(f'正在获取 {code} 的周K线数据...')
            data = self.sina.get_stock_history_data_bycode(code, scale=1200, datalen=weeks)
            if not data:
                print(f'  {code}: 无数据')
                continue

            df = pd.DataFrame(data)
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['day'] = pd.to_datetime(df['day'])
            df.sort_values('day', inplace=True)

            diff = df['close'].diff()
            df['direction'] = np.select([diff > 0, diff < 0], [1, -1], default=0)

            file_path = os.path.join(dir_name, f'{code}.xlsx')
            df.to_excel(file_path, index=False)
            print(f'  {code}: {len(df)} 条记录 -> {file_path}')

        print('完成')

    def run(self, args):
        """运行命令行接口"""
        # 验证数据长度参数
        datalen = self._validate_datalen(args.datalen)
        
        # 路由到对应的处理方法
        handlers = {
            'list': lambda: self._handle_list(),
            'count': lambda: self._handle_count(),
            'history': lambda: self._handle_history(args.history, args.scale, datalen),
            'kline': lambda: self._handle_kline(args.kline, args.scale, datalen),
            'score': lambda: self._handle_score(args.score, args.strategy, args.risk),
            'code': lambda: self._handle_code(args.code, args.scale, datalen),
            'suggest': lambda: self._handle_suggest(args.top, args.risk, args.strategy),
            'export': lambda: self._handle_export(args.weeks, args.datadir),
        }
        
        # 查找并执行对应的处理函数
        for action, handler in handlers.items():
            if getattr(args, action, False):
                handler()
                return
        
        # 没有匹配的操作，显示帮助
        parser = self.build_parser()
        parser.print_help()
    
    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        """构建命令行参数解析器"""
        parser = argparse.ArgumentParser(
            prog='stock_service',
            description='基于新浪财经 API，提供沪深 A 股实时行情查询、历史 K 线获取、K 线图绘制，以及多策略买入评分系统。',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='''
示例用法:
  %(prog)s --list                    获取全部A股行情列表
  %(prog)s --history sh600000        获取600000的日K线数据
  %(prog)s --kline sh600000          获取并绘制K线图
  %(prog)s --score sh600000          评估买入评分
  %(prog)s --code 600000             根据纯数字代码查询（自动匹配交易所）
  %(prog)s --suggest                 批量筛选推荐股票
  %(prog)s --suggest --top 20        筛选前20只推荐股票

策略选项 (--strategy):
  technical   技术面分析 (MA/成交量/ATR/突破)
  fundamental 基本面分析 (PE/PB/市值/流通率)
  trend       趋势跟踪 (MACD/布林/RSI/均线)
  all         综合模式 (三种策略加权)

风险偏好 (--risk):
  conservative 保守型 (要求严格)
  balanced     稳健型 (默认)
  aggressive   激进型 (放宽带宽)
'''
        )
        
        parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
        
        # 互斥操作组
        action_group = parser.add_mutually_exclusive_group()
        action_group.add_argument('--list', action='store_true', help='获取沪深A股完整列表')
        action_group.add_argument('--count', action='store_true', help='获取股票总数')
        action_group.add_argument('--history', metavar='SYMBOL', help='获取历史K线 (如 sh600000)')
        action_group.add_argument('--kline', metavar='SYMBOL', help='获取并绘制K线图')
        action_group.add_argument('--score', metavar='SYMBOL', help='买入评估评分')
        action_group.add_argument('--code', metavar='CODE', help='根据纯数字代码查询 (自动匹配交易所)')
        action_group.add_argument('--suggest', action='store_true', help='获取推荐股票列表 (批量筛选)')
        action_group.add_argument('--export', action='store_true', help='批量导出周K线数据到Excel')
        
        # 通用参数
        parser.add_argument('--top', type=int, default=10, 
                          help='返回股票数量 (默认: 10)')
        parser.add_argument('--scale', type=int, default=240,
                          choices=[5, 15, 30, 60, 240, 1200, 7200],
                          help='K线周期: 5/15/30/60分钟, 240日线, 1200周线, 7200月线 (默认: 240)')
        parser.add_argument('--datalen', type=int, default=1024,
                          help='返回数据条数, 范围1-1024 (默认: 1024)')
        parser.add_argument('--strategy', choices=['technical', 'fundamental', 'trend', 'all'],
                          default='all', help='评分策略 (默认: all)')
        parser.add_argument('--risk', choices=['conservative', 'balanced', 'aggressive'],
                          default='balanced', help='风险偏好 (默认: balanced)')
        parser.add_argument('--weeks', type=int, default=55,
                          help='导出周K线数据条数 (默认: 55，约一年)')
        parser.add_argument('--datadir', type=str, default='data.txt',
                          help='股票代码文件路径 (默认: data.txt)')
        
        return parser


def main():
    """主函数"""
    # 在程序启动时设置编码（可选，根据环境决定）
    if sys.platform == 'win32':
        # Windows 环境特殊处理
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleCP(65001)
            kernel32.SetConsoleOutputCP(65001)
        except:
            pass
    
    cli = StockServiceCLI()
    parser = cli.build_parser()
    args = parser.parse_args()
    
    try:
        cli.run(args)
    except KeyboardInterrupt:
        print('\n操作已取消', file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f'错误: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()