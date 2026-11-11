"""Run the development CLI without installing the package."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

