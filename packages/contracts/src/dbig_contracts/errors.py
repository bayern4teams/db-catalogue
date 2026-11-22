"""The one error type that command line entry points convert into a clean message."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

# `${NAME}` inside a TOML string (a value, a list item or a key) reads the environment variable
# NAME (owner decision 11, 2026-11-21: plain settings live in deploy/.env, committed config files
# keep placeholders). `$$` writes a literal `$`. Expansion runs on the parsed document, so comment
# text is never read and a value can never change the TOML structure (review 2026-11-22).
_ENV_PLACEHOLDER = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_placeholders(value: Any, path: Path) -> Any:
    """Return `value` with `${NAME}` replaced in every string, key and list item.

    An unset or blank variable is an operator error naming the setting to fix (J11).
    """
    if isinstance(value, dict):
        return {
            _expand_placeholders(key, path): _expand_placeholders(item, path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_expand_placeholders(item, path) for item in value]
    if not isinstance(value, str):
        return value

    def piece(placeholder: re.Match) -> str:
        if placeholder.group(0) == "$$":
            return "$"
        name = placeholder.group(1)
        found = os.environ.get(name)
        if found is None or not found.strip():
            raise OperatorError(
                f"configuration {path} uses ${{{name}}}, but the environment variable "
                f"{name} is unset or blank; set it in deploy/.env (or the shell) and "
                "run docker compose up -d again"
            )
        return found

    return _ENV_PLACEHOLDER.sub(piece, value)


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
    """Read one `[section]` table from a TOML configuration file, as an operator error.

    `${NAME}` inside a string reads the environment variable NAME after parsing
    (`_expand_placeholders`), so a committed file keeps placeholders while the plain values
    live in the deployment's env (owner decision 11, 2026-11-21).
    """
    try:
        data = _expand_placeholders(tomllib.loads(path.read_text(encoding="utf-8")), path)
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
