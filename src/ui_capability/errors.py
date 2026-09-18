"""Exceptions expose only enumerated codes, never adapter or validation messages."""

from .contracts import Diagnostic, FailureCode


class RuntimeFault(Exception):
    def __init__(self, code: FailureCode, diagnostic: Diagnostic | None = None) -> None:
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(code.value)
