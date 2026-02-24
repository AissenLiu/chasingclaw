"""PyInstaller entrypoint for a portable chasingclaw Web UI executable."""

from __future__ import annotations

import argparse

from chasingclaw.webui.server import WebUIServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chasingclaw-ui",
        description="Start chasingclaw Web UI server",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=18789, help="Bind port")
    parser.add_argument("--open", action="store_true", help="Open browser automatically")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    server = WebUIServer(host=args.host, port=args.port)
    server.serve(open_browser=args.open)


if __name__ == "__main__":
    main()
