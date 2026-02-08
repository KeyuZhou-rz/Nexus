from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Task


class Aggregator(ABC):
    """Base class for data source integrations."""

    name: str

    @abstractmethod
    def fetch_tasks(self) -> list[Task]:
        raise NotImplementedError
