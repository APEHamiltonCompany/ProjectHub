from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tracker.hub.app import create_app

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hub", description="Local project hub")
    parser.add_argument("--config", default=str(ROOT / "tracker" / "config.yaml"))
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-refresh", action="store_true")
    args = parser.parse_args(argv)

    if sys.stdout is None or sys.stderr is None:
        log_path = ROOT / "output" / "hub.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = handle
        if sys.stderr is None:
            sys.stderr = handle

    config_path = Path(args.config)
    if not config_path.is_file():
        sys.stderr.write("No config at %s. Run setup.cmd in the ProjectHub folder first.\n" % config_path)
        return 2

    app = create_app(args.config, port=args.port, no_refresh=args.no_refresh)
    app.run(host="127.0.0.1", port=int(app.config["HUB_PORT"]), debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
