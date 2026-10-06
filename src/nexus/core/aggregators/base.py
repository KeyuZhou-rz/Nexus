from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Task


class Aggregator(ABC):
    """Abstract base class for all data source integrations."""

    name: str

    @abstractmethod
    def fetch_tasks(self) -> list[Task]:
        """Must be implemented by subclasses to return a list of Task objects."""
        raise NotImplementedError
