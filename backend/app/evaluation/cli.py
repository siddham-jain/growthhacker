"""python -m app.evaluation <group> <command>: each group registers itself in command_registry."""

import argparse
import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.bootstrap import load_registrations
from app.core.config import Settings, get_settings
from app.core.container import Container, build_container
from app.core.registry import Registry

COMMAND_MODULES: tuple[str, ...] = (
    "app.evaluation.freeze.commands",
    "app.evaluation.backtest.commands",
    "app.evaluation.labelling.commands",
    "app.evaluation.review.commands",
    "app.evaluation.ops.commands",
    "app.evaluation.policy.commands",
)


@dataclass
class EvaluationRuntime:
    """lazy: commands that only render code-held data never open the database."""

    settings: Settings
    _container: Container | None = field(default=None, init=False)

    @property
    def container(self) -> Container:
        if self._container is None:
            load_registrations()
            self._container = build_container(self.settings)
        return self._container

    async def aclose(self) -> None:
        if self._container is not None:
            await self._container.aclose()


Handler = Callable[[argparse.Namespace, EvaluationRuntime], Awaitable[int]]
# each entry adds one sub-parser group and sets `handler` on its leaf commands
command_registry: Registry[Callable[[Any], None]] = Registry("evaluation_command_registry")


def build_parser() -> argparse.ArgumentParser:
    for module in COMMAND_MODULES:
        importlib.import_module(module)
    parser = argparse.ArgumentParser(prog="python -m app.evaluation", description=__doc__)
    groups = parser.add_subparsers(dest="group", required=True)
    for register in command_registry.all().values():
        register(groups)
    return parser


async def dispatch(args: argparse.Namespace, settings: Settings | None = None) -> int:
    runtime = EvaluationRuntime(settings or get_settings())
    try:
        return await args.handler(args, runtime)
    finally:
        await runtime.aclose()
