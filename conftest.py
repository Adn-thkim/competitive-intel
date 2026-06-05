"""pytest 루트 conftest.

scripts/ 하위 테스트가 `import server.*`를 사용할 수 있도록
프로젝트 루트(competitive-intel/)를 sys.path에 추가합니다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
