from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import httpx

from ame.config import Settings
from ame.contracts.schemas import TrendSignalIn


class TrendAdapter(ABC):
    """Official-API or permitted public source. Skip when unconfigured; never scrape."""

    name: str

    def is_configured(self, settings: Settings) -> bool:
        return True

    @abstractmethod
    async def fetch(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        now: datetime,
    ) -> list[TrendSignalIn]:
        raise NotImplementedError
