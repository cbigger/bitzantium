#!/usr/bin/env python3
"""
serve.py — Static site server for Bitzantium.

Serves the landing page and static files (skill, heartbeat, docs).
No API routes, no logic — just files.

    python serve.py [--port 8000] [--host 0.0.0.0]
"""

import argparse
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(os.path.dirname(__file__), "static"), **kwargs)


def main():
    parser = argparse.ArgumentParser(description="Bitzantium static site server")
    parser.add_argument("--port", "-p", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
