import os
import sys
import time
import numpy as np
from datetime import datetime
from sina_service import StockSinaService


def main():
    sina = StockSinaService()

    codes = []
    with open('data.txt', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and '\t' in line:
                codes.append(line.split('\t')[-1].strip())
            elif line and not line.startswith('#'):
                codes.append(line.split()[-1].strip())

    dir_name = datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(dir_name, exist_ok=True)
    print(f'输出目录: {dir_name}')

    no_data_codes = []

    for code in codes:
        print(f'正在获取 {code} 的周K线数据...')
        data = sina.get_stock_history_data_bycode(code, scale=1200, datalen=55)
        if not data:
            print(f'  {code}: 无数据')
            no_data_codes.append(code)
            continue

        import pandas as pd
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

    print(f'\n共 {len(no_data_codes)} 只股票无数据:')
    for c in no_data_codes:
        print(f'  {c}')


if __name__ == '__main__':
    main()
