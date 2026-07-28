"""Verify pre-start configuration."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()
from app.integrations.opencode_server import OPENCODE_EXE
print(f"OpenCode exe: {OPENCODE_EXE}")
print(f"Does it exist: {os.path.isfile(OPENCODE_EXE)}")
from app.supervisor.projects import get_projects
projs = get_projects()
print(f"Project aliases: {list(projs.keys())}")
for alias, info in projs.items():
    print(f"  {alias}: {info.get('path')} ({info.get('display_name')})")
