"""Command line: `tinkerpnp --config machine.yaml [--host 0.0.0.0] [--port 7300]` or `--check` to validate only."""
from __future__ import annotations

import argparse
import logging
import sys

from .core.config import load_config
from .core.registry import registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tinkerpnp", description="TinkerPnP pick-and-place host")
    parser.add_argument("--config", required=True, help="machine configuration (YAML)")
    parser.add_argument("--host", default="127.0.0.1", help="listen address (0.0.0.0 for the network)")
    parser.add_argument("--port", type=int, default=7300)
    parser.add_argument("--check", action="store_true", help="validate the configuration and exit")
    parser.add_argument("--ui", default=None, help="directory of the built web UI (default: ui/build in the repo)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    registry.load_plugins()
    cfg = load_config(args.config, strict=False)
    if cfg.problems:
        print(f"{args.config}: {len(cfg.problems)} problem(s)", file=sys.stderr)
        for p in cfg.problems:
            print(f"  {p.path or '(file)'}: {p.message}", file=sys.stderr)
        return 1
    if args.check:
        print(f"{args.config}: OK ({len(cfg.objects)} objects)")
        return 0

    import uvicorn

    from .api.server import create_app
    from .core.machine import Machine

    app = create_app(Machine(cfg), ui_dir=args.ui)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
