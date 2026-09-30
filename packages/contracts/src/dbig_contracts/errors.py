"""The one error type that command line entry points convert into a clean message."""

from __future__ import annotations


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
