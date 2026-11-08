# -*- coding: utf-8 -*-
"""Compatibility entry point for the modular Thornthwaite engine."""
from pathlib import Path
import sys
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from modules.bilan_hydrique_parts.workflow import main
if __name__ == "__main__":
    main()
