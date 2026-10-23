"""Canonical source/bundle paths; never infer them from a domain module."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DESKTOP_STATIC = ROOT / 'apps' / 'desktop' / 'renderer' / 'static'
