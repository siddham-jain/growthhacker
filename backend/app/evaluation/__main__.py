import asyncio
import sys

from app.evaluation.cli import build_parser, dispatch


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
