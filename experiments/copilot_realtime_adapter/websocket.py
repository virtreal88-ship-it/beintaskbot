"""Local test imports of the single server gateway candidate."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'beintaskbot-main'))
from tenant_operator_voice_gateway import *  # noqa: F401,F403
