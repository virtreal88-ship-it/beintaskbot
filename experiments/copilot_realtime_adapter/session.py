"""Local test import of the single production candidate implementation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'beintaskbot-main'))
from tenant_operator_voice_session import *  # noqa: F401,F403
