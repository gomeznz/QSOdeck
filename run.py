#!/usr/bin/env python3
"""Start QSODesk:  python3 run.py   then open http://localhost:8073"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qsodesk.server import serve  # noqa: E402

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="QSODesk station logger")
    ap.add_argument("--port", type=int, default=8073)
    ap.add_argument("--data", default=os.path.join(here, "data"),
                    help="folder for the log database and journal")
    args = ap.parse_args()
    serve(args.data, args.port)
