"""python -m subscribe — регенерация nodes.json.

Исходный sing-box-subscribe использует плоские импорты (import tool, groups,
parsers). Настраиваем sys.path так, чтобы работали и они, и общий пакет `naming`
из корня репозитория, затем запускаем main.py как скрипт.
"""

import os
import runpy
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))   # корень репо — для `import naming`
sys.path.insert(0, _here)                    # subscribe/ — для `import tool/groups/parsers`

runpy.run_path(os.path.join(_here, "main.py"), run_name="__main__")
