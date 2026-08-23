from __future__ import annotations

from typing import Any

import httpx

from ame.config import get_settings
from ame.contracts.enums import ConnectionState, Platform, PublishStatus
from ame.observability import get_logger
from ame.publishers.base import PreparedPublish, PublisherAdapter, PublishResult, ValidationResult
from ame.publishers.oauth import (
    connection_is_ready,
    connection_metadata,
    connection_scopes,
    http_client,
    is_simulated,
    load_access_token,
    load_media,
    load_refresh_token,
    persist_token_bundle,
    public_http_error,
    refresh_tiktok_token,
    token_needs_refresh,
)

logger = get_logger("ame.publishers.tiktok")

TIKTOK_CREATOR_INFO = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
TIKTOK_VIDEO_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
TIKTOK_VIDEO_QUERY = "https://open.tiktokapis.com/v2/video/query/"
CHUNK_SIZE = 10_000_000


def unattended_post_available(connection: Any) -> bool:
    scopes = connection_scopes(connection)
    meta = connection_metadata(connection)
    has_publish = "video.publish" in scopes
    reviewed = bool(
        meta.get("app_review_approved")
        or meta.get("unattended_post_available")
        or meta.get("audit_approved")
    )
    consent = bool(
        meta.get("unattended_post_consent")
        or meta.get("direct_post_enabled")
        or meta.get("creator_post_consent")
    )
    return has_publish and reviewed and consent


class TikTokPublisher(PublisherAdapter):
    platform = Platform.TIKTOK

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

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    async def validate(self, content: Any, connection: Any) -> ValidationResult:
        settings = get_settings()
        if is_simulated(content):
            return ValidationResult(
                ok=False,
                status=PublishStatus.REJECTED_SIMULATION,
                reasons=["production_publisher_refuses_simulation"],
            )
        if not settings.tiktok_client_key:
            return ValidationResult(
                ok=False,
                status=PublishStatus.CONNECTION_REQUIRED,
                reasons=["tiktok_client_key_missing"],
            )
        if not self._token(connection) and not connection_is_ready(connection):
            return ValidationResult(
                ok=False,
                status=PublishStatus.CONNECTION_REQUIRED,
                reasons=["tiktok_oauth_connection_required"],
            )
        if not unattended_post_available(connection):
            return ValidationResult(
                ok=False,
                status=PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL,
                reasons=["tiktok_app_review_or_consent_required"],
            )
        return ValidationResult(ok=True, status=PublishStatus.QUEUED, reasons=[])

    async def prepare(self, content: Any, asset: Any) -> PreparedPublish:
        media_key = str(getattr(asset, "storage_key", "") or "")
        title = str(getattr(content, "topic", None) or "AME")[:100]
        return PreparedPublish(
            content_id=content.id,
            platform=Platform.TIKTOK,
            title=title,
            description=title,
            media_key=media_key,
            metadata={"privacy_level": "SELF_ONLY"},
            simulation=is_simulated(content),
        )

    async def publish(self, prepared: PreparedPublish, *, idempotency_key: str) -> PublishResult:
        if prepared.simulation:
            return PublishResult(
                status=PublishStatus.REJECTED_SIMULATION,
                error="production_publisher_refuses_simulation",
                simulation=True,
            )
        settings = get_settings()
        token = self._token()
        if not settings.tiktok_client_key or not token:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="tiktok_oauth_connection_required",
                simulation=False,
            )
        if not unattended_post_available(self._connection):
            return PublishResult(
                status=PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL,
                error="tiktok_app_review_or_consent_required",
                simulation=False,
            )
        if not prepared.media_key and not prepared.metadata.get("video_url"):
            return PublishResult(
                status=PublishStatus.FAILED,
                error="media_missing",
                simulation=False,
            )
        async with http_client(self._client, timeout=180.0) as http:
            creator = await self._creator_info(http, token)
            if creator.status != PublishStatus.QUEUED:
                return creator
            privacy_options = list((creator.raw.get("privacy_level_options") or []))
            requested = str(prepared.metadata.get("privacy_level") or "SELF_ONLY")
            if requested in privacy_options:
                privacy = requested
            elif privacy_options:
                privacy = str(privacy_options[0])
            else:
                privacy = "SELF_ONLY"
            init = await self._init_direct_post(http, token, prepared, privacy)
            if init.status != PublishStatus.PROCESSING:
                return init
            upload_url = (init.raw or {}).get("upload_url")
            if upload_url:
                sent = await self._upload_file(http, str(upload_url), prepared.media_key)
                if sent.status != PublishStatus.PROCESSING:
                    return sent
            status = await self.get_status(init.external_id or "")
        status.raw = {
            **status.raw,
            **init.raw,
            "idempotency_key": idempotency_key,
            "privacy_level": privacy,
            "real_platform_post": status.status == PublishStatus.PUBLISHED,
        }
        return status

    async def _creator_info(self, http: httpx.AsyncClient, token: str) -> PublishResult:
        response = await http.post(TIKTOK_CREATOR_INFO, headers=self._headers(token), json={})
        if response.status_code == 401:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="tiktok_token_invalid",
                simulation=False,
            )
        payload = response.json()
        error = payload.get("error") or {}
        if error.get("code") not in {None, "", "ok"}:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=str(error.get("message") or error.get("code")),
                raw={"creator_info": payload},
                simulation=False,
            )
        data = payload.get("data") or {}
        return PublishResult(
            status=PublishStatus.QUEUED,
            raw=data,
            simulation=False,
        )

    async def _init_direct_post(
        self,
        http: httpx.AsyncClient,
        token: str,
        prepared: PreparedPublish,
        privacy: str,
    ) -> PublishResult:
        public_url = prepared.metadata.get("video_url")
        if public_url:
            source_info: dict[str, Any] = {"source": "PULL_FROM_URL", "video_url": public_url}
        else:
            data, _path = load_media(prepared.media_key)
            size = len(data)
            chunk = min(CHUNK_SIZE, size) or size
            total_chunks = max(1, (size + chunk - 1) // chunk)
            source_info = {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk,
                "total_chunk_count": total_chunks,
            }
        body = {
            "post_info": {
                "title": (prepared.description or prepared.title)[:2200],
                "privacy_level": privacy,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "is_aigc": bool(prepared.metadata.get("is_aigc", False)),
            },
            "source_info": source_info,
        }
        response = await http.post(TIKTOK_VIDEO_INIT, headers=self._headers(token), json=body)
        payload = response.json() if response.content else {}
        error = payload.get("error") or {}
        code = error.get("code")
        if code == "scope_not_authorized":
            return PublishResult(
                status=PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL,
                error="tiktok_video_publish_scope_missing",
                simulation=False,
            )
        if code == "unaudited_client_can_only_post_to_private_accounts":
            return PublishResult(
                status=PublishStatus.AWAITING_PLATFORM_REQUIRED_APPROVAL,
                error="tiktok_app_review_required",
                simulation=False,
            )
        if not response.is_success or code not in {None, "", "ok"}:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=str(error.get("message") or public_http_error(response)),
                raw={"http_status": response.status_code},
                simulation=False,
            )
        data = payload.get("data") or {}
        publish_id = data.get("publish_id")
        if not publish_id:
            return PublishResult(
                status=PublishStatus.FAILED,
                error="tiktok_publish_id_missing",
                simulation=False,
            )
        logger.info("tiktok_direct_post_initialized")
        return PublishResult(
            status=PublishStatus.PROCESSING,
            external_id=str(publish_id),
            raw={"upload_url": data.get("upload_url"), "publish_id": publish_id},
            simulation=False,
        )

    async def _upload_file(self, http: httpx.AsyncClient, upload_url: str, media_key: str) -> PublishResult:
        data, _path = load_media(media_key)
        total = len(data)
        offset = 0
        chunk_size = min(CHUNK_SIZE, total) or total
        while offset < total:
            end = min(offset + chunk_size, total) - 1
            chunk = data[offset : end + 1]
            response = await http.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{total}",
                },
                content=chunk,
            )
            if not response.is_success:
                return PublishResult(
                    status=PublishStatus.FAILED,
                    error=public_http_error(response),
                    raw={"http_status": response.status_code},
                    simulation=False,
                )
            offset = end + 1
        logger.info("tiktok_upload_completed", bytes=total)
        return PublishResult(status=PublishStatus.PROCESSING, simulation=False)

    async def get_status(self, external_id: str) -> PublishResult:
        token = self._token()
        if not token:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="tiktok_oauth_connection_required",
                simulation=False,
            )
        async with http_client(self._client) as http:
            response = await http.post(
                TIKTOK_STATUS,
                headers=self._headers(token),
                json={"publish_id": external_id},
            )
        payload = response.json() if response.content else {}
        error = payload.get("error") or {}
        if response.status_code == 401 or error.get("code") == "access_token_invalid":
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="tiktok_token_invalid",
                simulation=False,
            )
        data = payload.get("data") or {}
        status_name = str(data.get("status") or "")
        post_id = data.get("publically_available_post_id") or data.get(
            "publicly_available_post_id"
        )
        if status_name == "PUBLISH_COMPLETE":
            return PublishResult(
                status=PublishStatus.PUBLISHED,
                external_id=str(post_id or external_id),
                raw={"tiktok": data},
                simulation=False,
            )
        if status_name == "FAILED":
            return PublishResult(
                status=PublishStatus.FAILED,
                error=str(data.get("fail_reason") or "tiktok_publish_failed"),
                external_id=external_id,
                raw={"tiktok": data},
                simulation=False,
            )
        return PublishResult(
            status=PublishStatus.PROCESSING,
            external_id=external_id,
            raw={"tiktok": data},
            simulation=False,
        )

    async def fetch_metrics(self, publication: Any) -> dict[str, Any]:
        token = self._token()
        video_id = getattr(publication, "external_id", None)
        if not token or not video_id or str(video_id).startswith("v_pub_"):
            return {"raw": {}, "analytics_available": False}
        async with http_client(self._client) as http:
            response = await http.post(
                f"{TIKTOK_VIDEO_QUERY}?fields=id,like_count,comment_count,share_count,view_count",
                headers=self._headers(token),
                json={"filters": {"video_ids": [str(video_id)]}},
            )
        if not response.is_success:
            return {
                "raw": {},
                "analytics_available": False,
                "reason": public_http_error(response),
            }
        payload = response.json()
        videos = ((payload.get("data") or {}).get("videos")) or []
        if not videos:
            return {"raw": payload, "analytics_available": False}
        item = videos[0]
        return {
            "views": int(item.get("view_count") or 0),
            "likes": int(item.get("like_count") or 0),
            "comments": int(item.get("comment_count") or 0),
            "shares": int(item.get("share_count") or 0),
            "raw": payload,
            "analytics_available": True,
            "simulation": False,
        }

    async def refresh_auth(self, connection: Any) -> ConnectionState:
        settings = get_settings()
        if not settings.tiktok_client_key:
            return ConnectionState.CONNECTION_REQUIRED
        refresh = load_refresh_token(connection)
        if not refresh:
            return (
                ConnectionState.NEEDS_REAUTHORIZATION
                if load_access_token(connection)
                else ConnectionState.CONNECTION_REQUIRED
            )
        if load_access_token(connection) and not token_needs_refresh(connection):
            state = (
                ConnectionState.READY
                if unattended_post_available(connection)
                else ConnectionState.NEEDS_PLATFORM_REVIEW
            )
            connection.state = state.value
            return state
        try:
            bundle = await refresh_tiktok_token(refresh, client=self._client)
        except httpx.HTTPError:
            logger.info("tiktok_refresh_failed")
            connection.state = ConnectionState.NEEDS_REAUTHORIZATION.value
            return ConnectionState.NEEDS_REAUTHORIZATION
        persist_token_bundle(connection, bundle)
        extra = bundle.extra
        if extra.get("open_id"):
            meta = connection_metadata(connection)
            meta["open_id"] = extra["open_id"]
            connection.metadata_json = meta
        self._access_token = bundle.access_token
        self._connection = connection
        state = (
            ConnectionState.READY
            if unattended_post_available(connection)
            else ConnectionState.NEEDS_PLATFORM_REVIEW
        )
        connection.state = state.value
        return state
