class AppError(Exception):
    """Expected error that the API turns into the standard error envelope."""

    status_code = 500
    code = "INTERNAL"

    def __init__(self, message: str, details: list[dict] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidRequest(AppError):
    status_code = 422
    code = "INVALID_REQUEST"


class AoiInvalid(AppError):
    status_code = 422
    code = "AOI_INVALID"


class RangeTooLong(AppError):
    status_code = 422
    code = "RANGE_TOO_LONG"


class ExportTooLarge(AppError):
    status_code = 413
    code = "EXPORT_TOO_LARGE"


class DataStoreUnavailable(AppError):
    status_code = 503
    code = "DATA_STORE_UNAVAILABLE"

    def __init__(self, message: str = "The data store is not responding. Try again in a few minutes.") -> None:
        super().__init__(message)
