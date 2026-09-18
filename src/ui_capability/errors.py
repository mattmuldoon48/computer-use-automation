"""Exceptions expose only enumerated codes, never adapter or validation messages."""

from .contracts import FailureCode


class RuntimeFault(Exception):
    def __init__(self, code: FailureCode) -> None:
        self.code = code
        super().__init__(code.value)
