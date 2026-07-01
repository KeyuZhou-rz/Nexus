"""
Canvas LMS Connector — Nexus Phase X3

Implements LMSConnector for Canvas (Instructure) via REST API + OAuth2 token.

Setup:
  1. Generate a token at: <canvas-url>/profile/settings (Approved Integrations)
  2. Set env var CANVAS_API_TOKEN or pass token directly.
  3. Set env var CANVAS_BASE_URL (e.g. https://canvas.instructure.com).

Usage:
    connector = CanvasLMSConnector(
        base_url="https://canvas.instructure.com",
        token="...",
    )
    courses = connector.list_courses()
"""
from __future__ import annotations

import logging
from datetime import datetime

from .lms_base import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    LMSConnector,
)

logger = logging.getLogger(__name__)


class CanvasLMSConnector(LMSConnector):
    """
    LMSConnector for Canvas (Instructure) via REST API.

    Args:
        base_url: Canvas instance URL (e.g. https://canvas.instructure.com)
        token: Canvas API access token
        per_page: Number of results per page (max 100)
    """

    name = "canvas"

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        per_page: int = 50,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.per_page = min(per_page, 100)

    # ── LMSConnector interface ──────────────────────────────────────

    def list_courses(self) -> list[Course]:
        """GET /api/v1/courses?enrollment_state=active"""
        data = self._paginate(f"{self.base_url}/api/v1/courses", params={
            "enrollment_state": "active",
            "include[]": ["term"],
        })
        courses: list[Course] = []
        for item in data:
            cid = str(item.get("id", ""))
            courses.append(Course(
                course_id=cid,
                name=item.get("name") or item.get("course_code") or f"Canvas-{cid}",
                term=item.get("term", {}).get("name") if isinstance(item.get("term"), dict) else None,
                url=f"{self.base_url}/courses/{cid}",
                meta={"canvas_id": item.get("id")},
            ))
        return courses

    def list_assignments(self, course_id: str) -> list[Assignment]:
        """GET /api/v1/courses/:id/assignments"""
        data = self._paginate(
            f"{self.base_url}/api/v1/courses/{course_id}/assignments",
            params={"include[]": ["submission"]},
        )
        assignments: list[Assignment] = []
        for item in data:
            aid = str(item.get("id", ""))
            due_str = item.get("due_at")
            due_at = None
            if due_str:
                try:
                    due_at = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    due_at = None
            assignments.append(Assignment(
                assignment_id=aid,
                title=item.get("name", "Untitled Assignment"),
                course_id=course_id,
                due_at=due_at,
                url=item.get("html_url"),
                points_possible=item.get("points_possible"),
                description=item.get("description"),
                meta={"canvas_id": item.get("id")},
            ))
        return assignments

    def list_announcements(self, course_id: str) -> list[Announcement]:
        """GET /api/v1/announcements?context_codes[]=course_<id>"""
        data = self._paginate(
            f"{self.base_url}/api/v1/announcements",
            params={"context_codes[]": f"course_{course_id}"},
        )
        announcements: list[Announcement] = []
        for item in data:
            aid = str(item.get("id", ""))
            posted_str = item.get("posted_at") or item.get("created_at")
            posted_at = None
            if posted_str:
                try:
                    posted_at = datetime.fromisoformat(posted_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    posted_at = None
            announcements.append(Announcement(
                announcement_id=aid,
                title=item.get("title", "Untitled Announcement"),
                course_id=course_id,
                body=item.get("message"),
                posted_at=posted_at,
                url=item.get("url"),
                meta={"canvas_id": item.get("id")},
            ))
        return announcements

    def list_files(self, course_id: str) -> list[CourseFile]:
        """GET /api/v1/courses/:id/files"""
        data = self._paginate(
            f"{self.base_url}/api/v1/courses/{course_id}/files",
            params={"include[]": ["usage_rights"]},
        )
        files: list[CourseFile] = []
        for item in data:
            fid = str(item.get("id", ""))
            files.append(CourseFile(
                file_id=fid,
                name=item.get("display_name") or item.get("filename") or "unnamed",
                course_id=course_id,
                url=item.get("url"),
                content_type=item.get("content-type") or item.get("mime_class"),
                size_bytes=item.get("size"),
                meta={"canvas_id": item.get("id")},
            ))
        return files

    # ── HTTP helper ─────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _paginate(self, url: str, params: dict | None = None) -> list[dict]:
        """
        Paginate through Canvas API results following Link headers.

        Canvas uses RFC 5988 Link headers for pagination:
          Link: <url>; rel="next"
        """
        try:
            import requests
        except ImportError:
            logger.warning("requests not installed; Canvas connector unavailable")
            return []

        if not self.token:
            logger.warning("Canvas token not set; returning empty result")
            return []

        params = dict(params or {})
        params["per_page"] = str(self.per_page)

        all_items: list[dict] = []
        next_url: str | None = url

        while next_url:
            try:
                resp = requests.get(
                    next_url,
                    headers=self._headers(),
                    params=params if next_url == url else None,
                    timeout=30,
                )
                if resp.status_code == 401:
                    logger.error("Canvas API returned 401 — invalid or expired token")
                    break
                if resp.status_code == 403:
                    logger.error("Canvas API returned 403 — insufficient permissions")
                    break
                resp.raise_for_status()
                items = resp.json()
                if isinstance(items, list):
                    all_items.extend(items)
                else:
                    all_items.append(items)

                # Parse Link header for next page
                next_url = None
                link_header = resp.headers.get("Link", "")
                for part in link_header.split(","):
                    if 'rel="next"' in part:
                        # Extract URL between < and >
                        start = part.find("<")
                        end = part.find(">")
                        if start >= 0 and end > start:
                            next_url = part[start + 1:end]
                        break
            except Exception as exc:
                logger.warning(f"Canvas API error at {next_url}: {exc}")
                break

        return all_items
