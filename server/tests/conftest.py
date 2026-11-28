import os
import sys
from pathlib import Path

# 保证 `import app.*` 可用（CI 里用 pip install -e .，本地直接跑 pytest 也行）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOCK", "1")
os.environ.setdefault("AUTH_REQUIRED", "0")
