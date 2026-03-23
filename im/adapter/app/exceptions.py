class AdapterError(Exception):
    status_code = 500
    error_code = 'adapter_error'

    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.message = message


class UnauthorizedError(AdapterError):
    status_code = 401
    error_code = 'unauthorized'


class HumHubAPIError(AdapterError):
    status_code = 502
    error_code = 'humhub_api_error'
