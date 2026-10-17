"""Run full US-stock or passive-fund research through the single-agent MVP."""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from research_agent.config import ConfigurationError, Settings
from research_agent.skill_loader import load_adapter


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; reading an environment file requires an explicit option."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        adapter = load_adapter()
    except ConfigurationError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    parser = argparse.ArgumentParser(
        description="DeepEquity — Agentic Company Research System"
    )
    parser.add_argument(
        "query", help="Full US-company or China OTC passive-fund research question"
    )
    parser.add_argument(
        "--ticker",
        help="Optional explicit ticker to prevent ambiguous company selection",
    )
    parser.add_argument(
        "--fund-code", help="Six-digit China OTC fund code; preserve leading zeros"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output-dir", type=Path, default=Path("data/runs"))
    parser.add_argument("--target-type", choices=["stock", "fund"])
    parser.add_argument(
        "--fund-type",
        choices=[
            kind
            for kind in adapter.routes["fund"].subtype_sections
            if kind in {"passive_index", "etf_feeder"}
        ],
    )
    parser.add_argument("--share-class")
    parser.add_argument("--market")
    parser.add_argument("--scope", choices=["full", "focused"])
    parser.add_argument("--execution-path", choices=["mvp"], default="mvp")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicitly load a local environment file without overwriting process variables",
    )
    arguments = parser.parse_args(argv)
    fund = arguments.target_type == "fund"
    if fund:
        import re

        if (
            not arguments.fund_code
            or not re.fullmatch(r"[0-9]{6}", arguments.fund_code)
            or arguments.ticker
            or arguments.fund_type not in {"passive_index", "etf_feeder"}
            or not arguments.share_class
            or not re.fullmatch(r"[A-Za-z]", arguments.share_class)
            or arguments.market not in {None, "CN"}
            or arguments.scope not in {None, "full"}
        ):
            parser.error(
                "Fund MVP requires --fund-code, --fund-type passive_index/etf_feeder, "
                "--share-class, CN market and full scope."
            )
    elif (
        not arguments.ticker
        or arguments.target_type not in {None, "stock"}
        or arguments.scope not in {None, "full"}
        or arguments.market not in {None, "US"}
        or arguments.fund_type
        or arguments.share_class
        or arguments.fund_code
    ):
        parser.error(
            "Stock MVP requires an explicit US stock ticker and full scope; use the explicit fund options for passive funds."
        )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if arguments.env_file:
        from dotenv import load_dotenv

        if not arguments.env_file.is_file():
            parser.error("The requested environment file does not exist.")
        load_dotenv(arguments.env_file, override=False)
    try:
        settings = Settings.from_env()
        from research_agent.mvp import run_mvp

        result = run_mvp(
            settings,
            query=arguments.query,
            ticker=arguments.fund_code if fund else arguments.ticker,
            as_of=arguments.as_of,
            output_dir=arguments.output_dir,
            **(
                {
                    "target_type": "fund",
                    "fund_type": arguments.fund_type,
                    "share_class": arguments.share_class.upper(),
                }
                if fund
                else {}
            ),
        )
    except ConfigurationError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    except (ValueError, OSError) as error:
        sys.stderr.write(
            f"Local configuration or output failed ({type(error).__name__}).\n"
        )
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
