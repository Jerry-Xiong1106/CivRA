"""Business persistence failures shared by repositories and callers."""

class CaseNotFound(KeyError):
    pass


class VersionConflict(ValueError):
    def __init__(self, expected: int, actual: int):
        self.expected, self.actual = expected, actual
        super().__init__(f"expected case_version {expected}, current version is {actual}")


class RequestConflict(ValueError):
    pass


class UpdateDeadlineExceeded(TimeoutError):
    pass
