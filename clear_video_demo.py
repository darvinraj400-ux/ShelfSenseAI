#!/usr/bin/env python
# clear_video_demo.py
# -------------------------------------------------
# Removes ONLY the dedicated video-demo dataset created by
# seed_video_demo.py:
#   - shop "Segamat Jaya Mini Mart" (+ its users, products, matches,
#     inventory, price history, sales, decisions)
#   - MarketRefreshRun rows marked triggered_by='demo'
#
# It never touches "Demo Retail Shop", owner@demo.my, product 1336,
# real market observations, or real pricing decisions.
#
# Usage:
#   ./venv/Scripts/python.exe clear_video_demo.py
# -------------------------------------------------
from app import app
from seed_video_demo import _clear_video_demo


def main():
    with app.app_context():
        removed = _clear_video_demo()
        print("[OK] cleared video demo:", removed)


if __name__ == "__main__":
    main()
