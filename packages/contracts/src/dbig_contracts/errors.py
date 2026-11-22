"""The one error type that command line entry points convert into a clean message."""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

# One `${NAME}` inside a quoted TOML string value pulls in the environment variable NAME
# (owner decision 11, 2026-11-21: plain settings live in deploy/.env, committed config files
# keep placeholders). `$$` before `${` writes a literal `${` without a lookup.
_ENV_PLACEHOLDER = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_QUOTED_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _expand_placeholders(text: str, path: Path) -> str:
    """Replace `${NAME}` inside quoted string values with the environment's value.

    Substitution runs only inside `"..."` strings, so a bare key or number can never pick up
    text from the environment. An unset variable is an operator error naming the setting to
    fix (J11); the value is re-escaped, so a value containing quotes or backslashes cannot
    break out of its string.
    """

    def one(match: re.Match) -> str:
        inner = match.group(0)[1:-1]

        def piece(placeholder: re.Match) -> str:
            if placeholder.group(0) == "$$":
                return "$"
            name = placeholder.group(1)
            value = os.environ.get(name)
            if value is None or not value.strip():
                raise OperatorError(
                    f"configuration {path} uses ${{{name}}}, but the environment variable "
                    f"{name} is unset or blank; set it in deploy/.env (or the shell) and "
                    "run docker compose up -d again"
                )
            return json.dumps(value)[1:-1]

        return '"' + _ENV_PLACEHOLDER.sub(piece, inner) + '"'

    return _QUOTED_STRING.sub(one, text)


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

    `${NAME}` inside a quoted string value reads the environment variable NAME first
    (`_expand_placeholders`), so a committed file keeps placeholders while the plain values
    live in the deployment's env (owner decision 11, 2026-11-21).
    """
    try:
        data = tomllib.loads(_expand_placeholders(path.read_text(encoding="utf-8"), path))
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
