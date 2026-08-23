from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ame.config import get_settings
from ame.contracts.enums import ConnectionState, Platform, PublishStatus
from ame.observability import get_logger
from ame.publishers.base import PreparedPublish, PublisherAdapter, PublishResult, ValidationResult
from ame.publishers.oauth import (
    connection_is_ready,
    connection_metadata,
    http_client,
    is_simulated,
    load_access_token,
    load_media,
    persist_token_bundle,
    public_http_error,
    refresh_instagram_token,
)

logger = get_logger("ame.publishers.instagram")

POLL_ATTEMPTS = 20
POLL_SECONDS = 2.0


class InstagramPublisher(PublisherAdapter):
    platform = Platform.INSTAGRAM

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

    def _version(self) -> str:
        return get_settings().instagram_graph_version

    def _graph(self, path: str) -> str:
        return f"https://graph.facebook.com/{self._version()}/{path.lstrip('/')}"

    async def validate(self, content: Any, connection: Any) -> ValidationResult:
        settings = get_settings()
        if is_simulated(content):
            return ValidationResult(
                ok=False,
                status=PublishStatus.REJECTED_SIMULATION,
                reasons=["production_publisher_refuses_simulation"],
            )
        if not settings.meta_app_id:
            return ValidationResult(
                ok=False,
                status=PublishStatus.CONNECTION_REQUIRED,
                reasons=["meta_app_id_missing"],
            )
        token = self._token(connection)
        if not token:
            return ValidationResult(
                ok=False,
                status=PublishStatus.CONNECTION_REQUIRED,
                reasons=["instagram_oauth_connection_required"],
            )
        state = getattr(connection, "state", None) if connection is not None else None
        blocked_states = {
            ConnectionState.REQUIRES_HUMAN_ACTION.value,
            ConnectionState.NEEDS_PLATFORM_REVIEW.value,
        }
        if state in blocked_states or not connection_is_ready(connection):
            return ValidationResult(
                ok=False,
                status=PublishStatus.REQUIRES_HUMAN_ACTION,
                reasons=["instagram_connection_not_ready"],
            )
        return ValidationResult(ok=True, status=PublishStatus.QUEUED, reasons=[])

    async def prepare(self, content: Any, asset: Any) -> PreparedPublish:
        media_key = str(getattr(asset, "storage_key", "") or "")
        title = str(getattr(content, "topic", None) or "AME")[:100]
        return PreparedPublish(
            content_id=content.id,
            platform=Platform.INSTAGRAM,
            title=title,
            description=title,
            media_key=media_key,
            metadata={"media_type": "REELS"},
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
        settings = get_settings()
        if not settings.meta_app_id or not token:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="instagram_oauth_connection_required",
                simulation=False,
            )
        ig_user_id = (
            prepared.metadata.get("ig_user_id")
            or connection_metadata(self._connection).get("ig_user_id")
        )
        if not ig_user_id:
            ig_user_id = await self._resolve_ig_user_id(token)
        if not ig_user_id:
            return PublishResult(
                status=PublishStatus.REQUIRES_HUMAN_ACTION,
                error="instagram_professional_account_required",
                simulation=False,
            )
        async with http_client(self._client, timeout=180.0) as http:
            container = await self._create_container(http, token, str(ig_user_id), prepared)
            if container.status != PublishStatus.PROCESSING:
                return container
            container_id = container.external_id or ""
            public_url = prepared.metadata.get("video_url")
            if not public_url:
                uploaded = await self._rupload(http, token, container_id, prepared.media_key)
                if uploaded.status != PublishStatus.PROCESSING:
                    return uploaded
            polled = await self._poll_container(http, token, container_id)
            if polled.status != PublishStatus.QUEUED:
                return polled
            published = await self._publish_container(http, token, str(ig_user_id), container_id)
        published.raw = {
            **published.raw,
            "idempotency_key": idempotency_key,
            "ig_user_id": ig_user_id,
            "container_id": container_id,
            "real_platform_post": published.status == PublishStatus.PUBLISHED,
        }
        return published

    async def _create_container(
        self,
        http: httpx.AsyncClient,
        token: str,
        ig_user_id: str,
        prepared: PreparedPublish,
    ) -> PublishResult:
        params: dict[str, Any] = {
            "media_type": "REELS",
            "caption": prepared.description or prepared.title,
            "access_token": token,
        }
        public_url = prepared.metadata.get("video_url")
        if public_url:
            params["video_url"] = public_url
        else:
            params["upload_type"] = "resumable"
        response = await http.post(self._graph(f"{ig_user_id}/media"), params=params)
        if response.status_code == 401:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="instagram_token_invalid",
                simulation=False,
            )
        if not response.is_success:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=public_http_error(response),
                raw={"http_status": response.status_code},
                simulation=False,
            )
        payload = response.json()
        container_id = payload.get("id")
        if not container_id:
            return PublishResult(
                status=PublishStatus.FAILED,
                error="instagram_container_id_missing",
                raw=payload,
                simulation=False,
            )
        logger.info("instagram_container_created", container_id=str(container_id))
        return PublishResult(
            status=PublishStatus.PROCESSING,
            external_id=str(container_id),
            raw={"container": payload, "uri": payload.get("uri")},
            simulation=False,
        )

    async def _rupload(
        self, http: httpx.AsyncClient, token: str, container_id: str, media_key: str
    ) -> PublishResult:
        if not media_key:
            return PublishResult(
                status=PublishStatus.FAILED,
                error="media_missing",
                simulation=False,
            )
        data, _path = load_media(media_key)
        url = f"https://rupload.facebook.com/ig-api-upload/{self._version()}/{container_id}"
        response = await http.post(
            url,
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(len(data)),
            },
            content=data,
        )
        if not response.is_success:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=public_http_error(response),
                raw={"http_status": response.status_code},
                simulation=False,
            )
        logger.info("instagram_rupload_completed", container_id=container_id, bytes=len(data))
        return PublishResult(
            status=PublishStatus.PROCESSING,
            external_id=container_id,
            raw={"rupload": True},
            simulation=False,
        )

    async def _poll_container(
        self, http: httpx.AsyncClient, token: str, container_id: str
    ) -> PublishResult:
        last = "IN_PROGRESS"
        for _ in range(POLL_ATTEMPTS):
            response = await http.get(
                self._graph(container_id),
                params={"fields": "status_code,status", "access_token": token},
            )
            if not response.is_success:
                return PublishResult(
                    status=PublishStatus.FAILED,
                    error=public_http_error(response),
                    external_id=container_id,
                    simulation=False,
                )
            last = str(response.json().get("status_code") or "IN_PROGRESS")
            if last == "FINISHED":
                return PublishResult(
                    status=PublishStatus.QUEUED,
                    external_id=container_id,
                    raw={"status_code": last},
                    simulation=False,
                )
            if last in {"ERROR", "EXPIRED"}:
                return PublishResult(
                    status=PublishStatus.FAILED,
                    error=f"instagram_container_{last.lower()}",
                    external_id=container_id,
                    raw={"status_code": last},
                    simulation=False,
                )
            await asyncio.sleep(POLL_SECONDS)
        return PublishResult(
            status=PublishStatus.PROCESSING,
            external_id=container_id,
            error="instagram_container_still_processing",
            raw={"status_code": last},
            simulation=False,
        )

    async def _publish_container(
        self, http: httpx.AsyncClient, token: str, ig_user_id: str, container_id: str
    ) -> PublishResult:
        response = await http.post(
            self._graph(f"{ig_user_id}/media_publish"),
            params={"creation_id": container_id, "access_token": token},
        )
        if not response.is_success:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=public_http_error(response),
                raw={"http_status": response.status_code, "container_id": container_id},
                simulation=False,
            )
        payload = response.json()
        media_id = payload.get("id")
        permalink = None
        if media_id:
            look = await http.get(
                self._graph(str(media_id)),
                params={"fields": "permalink,id", "access_token": token},
            )
            if look.is_success:
                permalink = look.json().get("permalink")
        logger.info("instagram_published", media_id=str(media_id) if media_id else None)
        return PublishResult(
            status=PublishStatus.PUBLISHED,
            external_id=str(media_id) if media_id else container_id,
            url=permalink,
            raw={"publish": payload},
            simulation=False,
        )

    async def _resolve_ig_user_id(self, token: str) -> str | None:
        async with http_client(self._client) as http:
            response = await http.get(
                self._graph("me/accounts"),
                params={"fields": "instagram_business_account,name", "access_token": token},
            )
        if not response.is_success:
            return None
        for page in response.json().get("data") or []:
            account = page.get("instagram_business_account") or {}
            ig_id = account.get("id")
            if ig_id:
                if self._connection is not None:
                    meta = connection_metadata(self._connection)
                    meta["ig_user_id"] = ig_id
                    self._connection.metadata_json = meta
                return str(ig_id)
        return None

    async def get_status(self, external_id: str) -> PublishResult:
        token = self._token()
        if not token:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="instagram_oauth_connection_required",
                simulation=False,
            )
        async with http_client(self._client) as http:
            response = await http.get(
                self._graph(external_id),
                params={"fields": "id,permalink,status_code,timestamp", "access_token": token},
            )
        if response.status_code == 401:
            return PublishResult(
                status=PublishStatus.CONNECTION_REQUIRED,
                error="instagram_token_invalid",
                simulation=False,
            )
        if not response.is_success:
            return PublishResult(
                status=PublishStatus.FAILED,
                error=public_http_error(response),
                external_id=external_id,
                simulation=False,
            )
        payload = response.json()
        code = payload.get("status_code")
        if code in {"IN_PROGRESS"}:
            status = PublishStatus.PROCESSING
        elif code in {"ERROR", "EXPIRED"}:
            status = PublishStatus.FAILED
        else:
            status = PublishStatus.PUBLISHED
        return PublishResult(
            status=status,
            external_id=external_id,
            url=payload.get("permalink"),
            raw={"instagram": payload},
            simulation=False,
        )

    async def fetch_metrics(self, publication: Any) -> dict[str, Any]:
        token = self._token()
        media_id = getattr(publication, "external_id", None)
        if not token or not media_id:
            return {"raw": {}, "analytics_available": False}
        async with http_client(self._client) as http:
            response = await http.get(
                self._graph(f"{media_id}/insights"),
                params={
                    "metric": "plays,reach,likes,comments,shares,saved,total_interactions",
                    "access_token": token,
                },
            )
        if not response.is_success:
            return {
                "raw": {},
                "analytics_available": False,
                "reason": public_http_error(response),
            }
        payload = response.json()
        mapped: dict[str, Any] = {}
        for item in payload.get("data") or []:
            name = item.get("name")
            values = item.get("values") or []
            if name and values:
                mapped[str(name)] = values[0].get("value")
        return {
            "views": int(mapped.get("plays") or 0),
            "likes": int(mapped.get("likes") or 0),
            "comments": int(mapped.get("comments") or 0),
            "shares": int(mapped.get("shares") or 0),
            "raw": payload,
            "analytics_available": True,
            "simulation": False,
        }

    async def refresh_auth(self, connection: Any) -> ConnectionState:
        settings = get_settings()
        if not settings.meta_app_id:
            return ConnectionState.CONNECTION_REQUIRED
        token = load_access_token(connection)
        if not token:
            return ConnectionState.CONNECTION_REQUIRED
        try:
            bundle = await refresh_instagram_token(token, client=self._client)
        except httpx.HTTPError:
            logger.info("instagram_refresh_failed")
            connection.state = ConnectionState.NEEDS_REAUTHORIZATION.value
            return ConnectionState.NEEDS_REAUTHORIZATION
        persist_token_bundle(connection, bundle)
        self._access_token = bundle.access_token
        self._connection = connection
        meta = connection_metadata(connection)
        if meta.get("ig_user_id"):
            connection.state = ConnectionState.READY.value
            return ConnectionState.READY
        connection.state = ConnectionState.CONNECTED.value
        return ConnectionState.CONNECTED
