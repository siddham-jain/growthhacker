from typing import Any


class YouTubeApiError(Exception):
    retryable = False

    def __init__(self, message: str, *, status: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class NotSent(YouTubeApiError):
    # the request provably never reached google, so its reserved units are refunded
    retryable = True


class Transient(YouTubeApiError):
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, status=status, reason=reason)
        self.retry_after_s = retry_after_s


class RateLimited(Transient):
    pass


class CircuitOpen(YouTubeApiError):
    pass


class GoogleQuotaExceeded(YouTubeApiError):
    pass


class KeyRejected(YouTubeApiError):
    pass


class Forbidden(YouTubeApiError):
    pass


class CommentsDisabled(YouTubeApiError):
    pass


class NotFound(YouTubeApiError):
    pass


class InvalidPageToken(YouTubeApiError):
    pass


class InvalidRequest(YouTubeApiError):
    pass


class Unauthorized(YouTubeApiError):
    pass


class InsufficientScope(YouTubeApiError):
    pass


class NoYouTubeChannel(YouTubeApiError):
    pass


QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
KEY_REASONS = {"keyInvalid", "keyExpired", "accessNotConfigured", "ipRefererBlocked", "forbiddenByApiKey"}
KEY_STATUS_REASONS = {
    "API_KEY_INVALID",
    "API_KEY_SERVICE_BLOCKED",
    "API_KEY_HTTP_REFERRER_BLOCKED",
    "API_KEY_IP_ADDRESS_BLOCKED",
    "SERVICE_DISABLED",
}
FORBIDDEN_REASONS = {"forbidden", "channelForbidden", "playlistItemsNotAccessible", "videoNotAccessible"}
NOT_FOUND_REASONS = {
    "channelNotFound",
    "playlistNotFound",
    "videoNotFound",
    "notFound",
    "commentThreadNotFound",
}


def _reasons(body: Any) -> tuple[str | None, set[str], str]:
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, set(), ""
    reasons = {item.get("reason") for item in error.get("errors") or [] if isinstance(item, dict)}
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reasons.add(detail["reason"])
    reasons.discard(None)
    first = next(iter(sorted(reasons)), None)
    return first, reasons, str(error.get("message") or "")


def classify_error(status: int, body: Any, *, retry_after_s: float | None = None) -> YouTubeApiError:
    first, reasons, message = _reasons(body)
    text = message or f"youtube api returned http {status}"

    def make(kind: type[YouTubeApiError], reason: str | None = None) -> YouTubeApiError:
        return kind(text, status=status, reason=reason or first)

    if reasons & QUOTA_REASONS:
        return make(GoogleQuotaExceeded, next(iter(reasons & QUOTA_REASONS)))
    if reasons & RATE_REASONS or status == 429:
        return RateLimited(text, status=status, reason=first, retry_after_s=retry_after_s)
    if reasons & (KEY_REASONS | KEY_STATUS_REASONS) or (status == 400 and "api key" in message.lower()):
        return make(KeyRejected)
    if "commentsDisabled" in reasons:
        return make(CommentsDisabled, "commentsDisabled")
    if "invalidPageToken" in reasons:
        return make(InvalidPageToken, "invalidPageToken")
    if "youtubeSignupRequired" in reasons:
        return make(NoYouTubeChannel, "youtubeSignupRequired")
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return make(InsufficientScope)
    if status == 401:
        return make(Unauthorized)
    if reasons & NOT_FOUND_REASONS or status == 404:
        return make(NotFound)
    if reasons & FORBIDDEN_REASONS or status == 403:
        return make(Forbidden)
    if status >= 500:
        return Transient(text, status=status, reason=first, retry_after_s=retry_after_s)
    return make(InvalidRequest)
