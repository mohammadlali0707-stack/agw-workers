#!/usr/bin/env python3
"""
qa/smoke_test_candidate_feeds.py

Convenience runner entrypoint for Tools/smoke_test_candidate_feeds.py
Author: agw-ACC5-w02
"""

import os
import sys

# Ensure repository root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from Tools.smoke_test_candidate_feeds import main

if __name__ == "__main__":
    main()
