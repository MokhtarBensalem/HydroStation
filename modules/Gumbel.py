#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entry point for the modular Gumbel engine."""
from pathlib import Path
import sys
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from modules.gumbel_parts.workflow import main
if __name__ == "__main__":
    main()
