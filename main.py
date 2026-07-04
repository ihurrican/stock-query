import sys
from stock_service import main

if __name__ == '__main__':
    if len(sys.argv) == 1:
        sys.argv.append('--export')
    main()
