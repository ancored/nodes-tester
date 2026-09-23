"""python -m subscribe — регенерация nodes.json (совместимая обёртка, см. main.py)."""

import os
import sys

# корень репо — для пакетов naming / nodes_common / nodes_fetch / nodes_config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subscribe.main import main  # noqa: E402

sys.exit(main())
