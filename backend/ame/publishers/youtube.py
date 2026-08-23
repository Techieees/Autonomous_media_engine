from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ame.config import get_settings
from ame.contracts.enums import ConnectionState, Platform, PublishStatus
from ame.observability import get_logger
from ame.publishers.base import PreparedPublish, PublisherAdapter, PublishResult, ValidationResult
from ame.publishers.oauth import (
    connection_is_ready,
    http_client,
    is_simulated,
    load_access_token,
    load_media,
    load_refresh_token,
    persist_token_bundle,
    public_http_error,
    refresh_youtube_token,
    token_needs_refresh,
)

logger = get_logger("ame.publishers.youtube")

YOUTUBE_UPLOAD_INIT = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
YOUTUBE_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
CHUNK_SIZE = 8 * 1024 * 1024


class YouTubePublisher(PublisherAdapter):
    platform = Platform.YOUTUBE

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        access_token: str | None = None,
        connection: Any = None,
    ) -> None:
        self._client = client
        self._access_token = access_token
        self._connection = connection

    def _token(self, connection: Any = None) -> str | None:
        if self._access_token:
            return self._access_token
        return load_access_token(connection if connection is not None else self._connection)

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def validate(self, content: Any, connection: Any) -> ValidationResult:
        settings = get_settings()
        if is_simulated(content):
            return ValidationResult(
                ok=False,
                status=PublishStatus.REJECTED_SIMULATION,
                reasons=["production_publisher_refuses_simulation"],
            )
        if not settings.youtube_client_id:
            return ValidationResult(
                ok=False,
                status=PublishStatus.CONNECTION_REQUIRED,
                reasons=["youtube_client_id_missing"],
            )
        if not connection_is_ready(connection) and not self._token(connection):
            return ValidationResult(
                ok=False,
                status=PublishStatus.CONNECTION_REQUIRED,
                reasons=["youtube_oauth_connection_required"],
            )
        return ValidationResult(ok=True, status=PublishStatus.QUEUED, reasons=[])

    async def prepare(self, content: Any, asset: Any) -> PreparedPublish:
        media_key = str(getattr(asset, "storage_key", "") or "")
        title = str(getattr(content, "topic", None) or "AME")[:100]
        privacy = str(getattr(content, "privacy_status", None) or "unlisted")
        return PreparedPublish(
            content_id=content.id,
            platform=Platform.YOUTUBE,
            title=title,
            description=title,
            media_key=media_key,
            metadata={"privacyStatus": privacy, "snippet": {"title": title}},
            simulation=is_simulated(content),
        )

    async def publish(self, prepared: PreparedPublish, *, idempotency_key: str) -> PublishResult:
        if prepared.simulation:
            return PublishResult(
                status=PublishStatus.REJECTED_SIMULATION,
                error="production_publisher_refuses_simulation",
                simulation=True,
            )
        token = self._token()
        if not token:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="youtube_oauth_connection_required",
                simulation=False,
            )
        if not prepared.media_key:
            return PublishResult(
                status=PublishStatus.FAILED,
                error="media_missing",
                simulation=False,
            )
        data, _path = load_media(prepared.media_key)
        privacy = str(prepared.metadata.get("privacyStatus") or "unlisted")
        snippet = {
            "title": prepared.title[:100],
            "description": prepared.description,
            "categoryId": str(prepared.metadata.get("categoryId") or "22"),
        }
        tags = prepared.metadata.get("tags") or prepared.metadata.get("hashtags")
        if tags:
            snippet["tags"] = [str(tag).lstrip("#") for tag in tags][:15]
        body = {
            "snippet": snippet,
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        async with http_client(self._client, timeout=300.0) as http:
            init = await http.post(
                YOUTUBE_UPLOAD_INIT,
                headers={
                    **self._auth_headers(token),
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(len(data)),
                },
                json=body,
            )
            if init.status_code == 401:
                return PublishResult(
                    status=PublishStatus.CONNECTION_REQUIRED,
                    error="youtube_token_invalid",
                    simulation=False,
                )
            if not init.is_success:
                return PublishResult(
                    status=PublishStatus.FAILED,
                    error=public_http_error(init),
                    raw={"http_status": init.status_code},
                    simulation=False,
                )
            upload_url = init.headers.get("Location") or init.headers.get("location")
            if not upload_url:
                return PublishResult(
                    status=PublishStatus.FAILED,
                    error="youtube_resumable_session_missing",
                    simulation=False,
                )
            logger.info(
                "youtube_resumable_started",
                content_id=str(prepared.content_id),
                bytes=len(data),
            )
            uploaded = await self._upload_resumable(http, upload_url, data)
        if uploaded.status != PublishStatus.PUBLISHED:
            return uploaded
        video_id = uploaded.external_id or ""
        uploaded.raw = {
            **uploaded.raw,
            "idempotency_key": idempotency_key,
            "privacyStatus": privacy,
            "snippet": snippet,
            "real_platform_post": True,
        }
        uploaded.url = (
            f"https://www.youtube.com/shorts/{video_id}"
            if prepared.metadata.get("shorts")
            else f"https://www.youtube.com/watch?v={video_id}"
        )
        return uploaded

    async def _upload_resumable(
        self, http: httpx.AsyncClient, upload_url: str, data: bytes
    ) -> PublishResult:
        total = len(data)
        offset = 0
        while offset < total:
            end = min(offset + CHUNK_SIZE, total) - 1
            chunk = data[offset : end + 1]
            response = await http.put(
                upload_url,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {offset}-{end}/{total}",
                },
                content=chunk,
            )
            if response.status_code in {200, 201}:
                payload = response.json()
                video_id = payload.get("id")
                if not video_id:
                    return PublishResult(
                        status=PublishStatus.FAILED,
                        error="youtube_video_id_missing",
                        raw={"http_status": response.status_code},
                        simulation=False,
                    )
                logger.info("youtube_resumable_completed", video_id=str(video_id))
                return PublishResult(
                    status=PublishStatus.PUBLISHED,
                    external_id=str(video_id),
                    raw={"youtube": payload},
                    simulation=False,
                )
            if response.status_code == 308:
                range_header = response.headers.get("Range") or response.headers.get("range")
                if range_header and "-" in range_header:
                    offset = int(range_header.split("-")[1]) + 1
                else:
                    offset = end + 1
                continue
            if response.status_code >= 500:
                probed = await self._probe_offset(http, upload_url, total)
                if probed is not None:
                    offset = probed
                    continue
            return PublishResult(
                status=PublishStatus.FAILED,
                error=public_http_error(response),
                raw={"http_status": response.status_code},
                simulation=False,
            )
        return PublishResult(status=PublishStatus.FAILED, error="youtube_upload_incomplete")

    async def _probe_offset(self, http: httpx.AsyncClient, upload_url: str, total: int) -> int | None:
        response = await http.put(
            upload_url,
            headers={"Content-Range": f"bytes */{total}", "Content-Length": "0"},
            content=b"",
        )
        if response.status_code == 308:
            range_header = response.headers.get("Range") or response.headers.get("range")
            if range_header and "-" in range_header:
                return int(range_header.split("-")[1]) + 1
            return 0
        return None

    async def get_status(self, external_id: str) -> PublishResult:
        token = self._token()
        if not token:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="youtube_oauth_connection_required",
                simulation=False,
            )
        async with http_client(self._client) as http:
            response = await http.get(
                YOUTUBE_VIDEOS,
                params={
                    "part": "status,snippet,processingDetails",
                    "id": external_id,
                },
                headers=self._auth_headers(token),
            )
        if response.status_code == 401:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="youtube_token_invalid",
                simulation=False,
            )
        if not response.is_success:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=public_http_error(response),
                raw={"http_status": response.status_code},
                simulation=False,
            )
        items = response.json().get("items") or []
        if not items:
            return PublishResult(
                status=PublishStatus.FAILED,
                error="youtube_video_not_found",
                external_id=external_id,
                simulation=False,
            )
        item = items[0]
        processing = (item.get("processingDetails") or {}).get("processingStatus")
        upload_status = (item.get("status") or {}).get("uploadStatus")
        if processing in {"processing", "started"} or upload_status == "uploaded":
            status = PublishStatus.PROCESSING
        elif upload_status == "failed" or processing == "failed":
            status = PublishStatus.FAILED
        else:
            status = PublishStatus.PUBLISHED
        return PublishResult(
            status=status,
            external_id=external_id,
            url=f"https://www.youtube.com/watch?v={external_id}",
            raw={"youtube": item},
            simulation=False,
        )

    async def fetch_metrics(self, publication: Any) -> dict[str, Any]:
        token = self._token()
        video_id = getattr(publication, "external_id", None)
        if not token or not video_id:
            return {"raw": {}, "analytics_available": False}
        end = datetime.now(UTC).date()
        created = getattr(publication, "created_at", None)
        start = (created.date() if created is not None else end - timedelta(days=30))
        async with http_client(self._client) as http:
            response = await http.get(
                YOUTUBE_ANALYTICS,
                params={
                    "ids": "channel==MINE",
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "metrics": (
                        "views,likes,comments,shares,"
                        "estimatedMinutesWatched,averageViewPercentage"
                    ),
                    "filters": f"video=={video_id}",
                },
                headers=self._auth_headers(token),
            )
        if not response.is_success:
            return {
                "raw": {},
                "analytics_available": False,
                "reason": public_http_error(response),
            }
        payload = response.json()
        headers = [col.get("name") for col in payload.get("columnHeaders") or []]
        row = (payload.get("rows") or [[]])[0]
        mapped = {name: row[index] for index, name in enumerate(headers) if index < len(row)}
        return {
            "views": int(mapped.get("views") or 0),
            "likes": int(mapped.get("likes") or 0),
            "comments": int(mapped.get("comments") or 0),
            "shares": int(mapped.get("shares") or 0),
            "watch_time_seconds": float(mapped.get("estimatedMinutesWatched") or 0) * 60,
            "completion_rate": mapped.get("averageViewPercentage"),
            "raw": payload,
            "analytics_available": True,
            "simulation": False,
        }

    async def refresh_auth(self, connection: Any) -> ConnectionState:
        settings = get_settings()
        if not settings.youtube_client_id:
            return ConnectionState.CONNECTION_REQUIRED
        refresh = load_refresh_token(connection)
        if not refresh:
            return (
                ConnectionState.NEEDS_REAUTHORIZATION
                if load_access_token(connection)
                else ConnectionState.CONNECTION_REQUIRED
            )
        if load_access_token(connection) and not token_needs_refresh(connection):
            connection.state = ConnectionState.READY.value
            return ConnectionState.READY
        try:
            bundle = await refresh_youtube_token(refresh, client=self._client)
        except httpx.HTTPError:
            logger.info("youtube_refresh_failed")
            connection.state = ConnectionState.NEEDS_REAUTHORIZATION.value
            return ConnectionState.NEEDS_REAUTHORIZATION
        persist_token_bundle(connection, bundle)
        self._access_token = bundle.access_token
        self._connection = connection
        connection.state = ConnectionState.READY.value
        return ConnectionState.READY
