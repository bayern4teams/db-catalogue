"""The one error type that command line entry points convert into a clean message."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError


class OperatorError(ValueError):
    """A failure the person running the command can fix from the message alone.

    An unknown identifier, state that has not been built yet, a feature that was not configured.
    These are not programming errors and not validation failures inside a record, so a traceback
    tells the operator nothing they can act on, while the message does.

    Subclasses `ValueError` so every existing caller and test that expects one keeps working.
    Each package's Click group converts this type, and only this type: catching bare `ValueError`
    would hide pydantic validation failures and genuine bugs behind a tidy message, which is why
    an ordinary `ValueError` deliberately keeps its full traceback.
    """


def describe_validation(error: ValidationError) -> str:
    """Name each failing setting and why, never the value it held.

    Pydantic's own text repeats the input, and a configuration or observation value can be a
    host name, a path or SQL text; the location and the rule are enough to fix it.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(top level)'}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False)
    )


def load_toml_section(path: Path, section: str) -> dict[str, Any]:
    """Read one `[section]` table from a TOML configuration file, as an operator error."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        # The decoder reports only a position, never the offending text.
        raise OperatorError(f"configuration {path} is not valid TOML: {error}") from error
    if set(data) != {section} or not isinstance(data[section], dict):
        raise OperatorError(f"configuration {path} must contain only a [{section}] table")
    return dict(data[section])


def validate_configuration[ModelT: BaseModel](
    path: Path, model: type[ModelT], values: dict[str, Any]
) -> ModelT:
    """Validate configuration values, reporting a mistake as something the operator can fix.

    Unlike a record inside an artifact, every value here was written by the person running the
    command, so a validation failure is theirs to correct from the message.
    """
    try:
        return model.model_validate(values)
    except ValidationError as error:
        raise OperatorError(
            f"configuration {path} is invalid: {describe_validation(error)}"
        ) from error
