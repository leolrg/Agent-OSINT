class ScanConfigError(Exception):
    """Invalid or incomplete scan configuration (e.g. missing API key)."""


class ScanStopped(Exception):
    """Raised when a scan hits a cap mid-flight; caught in scan() for synthesis."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
