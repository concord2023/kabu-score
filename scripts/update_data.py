"""Compatibility entry point for the legacy GitHub Actions workflow.

The project moved the canonical updater to the repository root.  Older
workflows still call scripts/update_data.py, so keep this tiny bridge to ensure
both workflow generations execute exactly the same updater.
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / 'update_data.py'), run_name='__main__')
