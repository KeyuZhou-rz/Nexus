"""Playwright async scraper for Brightspace (NYU SSO + Duo MFA)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .archive_utils import (
    build_archive_filename,
    is_supported_attachment,
    sanitize_segment,
)
from .session import load_session, save_session

# ── helpers ──────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    """Write a debug message to stderr (never pollutes stdout JSON)."""
    print(f"[archive_sync] {msg}", file=sys.stderr)


def _make_id(prefix: str, title: str, url: str | None, due: str | None) -> str:
    raw = f"{title}:{url}:{due}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _parse_due(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in (
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _archive_root_default() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "archives"


def _due_date_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.date().isoformat()


def _candidate_course_urls(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    return [
        f"{base}/d2l/home",
        f"{base}/d2l/home?isCourseNav=1",
    ]


def _normalize_login_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"manual", "auto", "hybrid"}:
        return value
    return "manual"


def _extract_ou_from_href(href: str) -> str | None:
    if not href:
        return None
    if "?ou=" in href:
        return href.split("?ou=")[-1].split("&")[0].strip() or None
    m = re.search(r"/d2l/home/(?P<ou>\d+)(?:/|$)", href)
    if m:
        return m.group("ou")
    m = re.search(r"[?&]ou=(?P<ou>\d+)", href)
    if m:
        return m.group("ou")
    return None


def _clean_course_title(value: str) -> str:
    text = " ".join((value or "").split()).strip()
    text = re.sub(r"\s*\|\s*Brightspace.*$", "", text, flags=re.IGNORECASE)
    return text


def _discover_courses_from_links(
    links: list[dict[str, str]],
    base_url: str,
) -> list[dict[str, str]]:
    seen: set[str] = set()
    courses: list[dict[str, str]] = []
    for link in links:
        href = str(link.get("href", "")).strip()
        if not href:
            continue
        full_href = urljoin(base_url + "/", href)
        ou = _extract_ou_from_href(full_href)
        if not ou or ou in seen:
            continue

        title_candidates = [
            str(link.get("text", "")).strip(),
            str(link.get("aria_label", "")).strip(),
            str(link.get("title", "")).strip(),
        ]
        title = _clean_course_title(next((t for t in title_candidates if t), ""))
        if not title or len(title) < 2:
            continue
        if title.lower() in {"home", "calendar", "grades", "content"}:
            continue

        seen.add(ou)
        courses.append({"name": title, "ou": ou})
    return courses


async def _capture_debug_artifacts(page, debug_dir: Path, stage: str) -> list[str]:
    artifacts: list[str] = []
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    stem = f"{stage}_{ts}"

    html_path = debug_dir / f"{stem}.html"
    png_path = debug_dir / f"{stem}.png"
    meta_path = debug_dir / f"{stem}.json"

    try:
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        artifacts.append(str(html_path.resolve()))
    except Exception as exc:
        _log(f"Failed to save debug html: {exc}")
    try:
        await page.screenshot(path=str(png_path), full_page=True)
        artifacts.append(str(png_path.resolve()))
    except Exception as exc:
        _log(f"Failed to save debug screenshot: {exc}")
    try:
        meta = {
            "url": page.url,
            "title": await page.title(),
            "stage": stage,
            "saved_at": datetime.now().astimezone().isoformat(),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(str(meta_path.resolve()))
    except Exception as exc:
        _log(f"Failed to save debug meta: {exc}")
    return artifacts


# ── Brightspace helpers ───────────────────────────────────────────────────────

def _is_sso_redirect(url: str) -> bool:
    sso_markers = (
        "shibboleth",
        "login.nyu.edu",
        "shib.nyu.edu",
        "idp.nyu.edu",
        "duo",
        "login.microsoftonline.com",
        "login.live.com",
    )
    return any(m in url.lower() for m in sso_markers)


def _normalize_login_username(username: str) -> str:
    value = (username or "").strip()
    if not value:
        return value
    if "@" in value:
        return value
    return f"{value}@nyu.edu"


async def _submit_sso_credentials(page, username: str, password: str) -> None:
    """Submit credentials across common NYU SSO screens."""
    normalized_username = _normalize_login_username(username)

    # Azure/NYU modern sign-in page.
    if "microsoftonline.com" in page.url.lower():
        email_selectors = "input[name='loginfmt'], input#i0116, input[type='email']"
        email_input = await page.query_selector(email_selectors)
        if email_input:
            await page.fill(email_selectors, normalized_username)
            await page.click("button[type='submit'], input[type='submit'], #idSIButton9")
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)

        password_selectors = "input[name='passwd'], input#i0118, input[type='password']"
        password_input = await page.query_selector(password_selectors)
        if password_input:
            await page.fill(password_selectors, password)
            await page.click("button[type='submit'], input[type='submit'], #idSIButton9")
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)

        # Optional "Stay signed in?" step.
        stay_prompt = await page.query_selector("#idSIButton9, #idBtn_Back")
        if stay_prompt:
            # Choose "No" if present to avoid sticky cross-session surprises.
            if await page.query_selector("#idBtn_Back"):
                await page.click("#idBtn_Back")
            else:
                await page.click("#idSIButton9")
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        return

    # Legacy Shibboleth page fallback.
    await page.fill(
        'input[name="j_username"], input[id="username"], input[name="username"]',
        normalized_username,
    )
    await page.fill(
        'input[name="j_password"], input[id="password"], input[name="password"]',
        password,
    )
    await page.click('button[type="submit"], input[type="submit"]')
    await page.wait_for_load_state("networkidle", timeout=15_000)


async def _wait_for_brightspace(page, base_url: str, timeout_s: int = 180) -> bool:
    """
    Poll until the browser lands back on Brightspace (after Duo).
    Returns True on success, False on timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        current = page.url
        if current.startswith(base_url) and not _is_sso_redirect(current):
            return True
        await asyncio.sleep(2)
    return False


async def _login_or_restore_session(
    context,
    base_url: str,
    username: str,
    password: str,
    *,
    login_mode: str = "manual",
) -> None:
    """
    1. Try loading saved session cookies.
    2. Navigate to Brightspace home; if redirected to SSO, do login.
    3. Wait for user to complete Duo MFA (up to 180 s).
    4. Save updated cookies.
    """
    cookies = load_session()
    if cookies:
        _log(f"Loaded {len(cookies)} saved cookies — attempting restore.")
        await context.add_cookies(cookies)

    page = await context.new_page()
    await page.goto(f"{base_url}/d2l/home", timeout=30_000)
    await page.wait_for_load_state("domcontentloaded")

    if _is_sso_redirect(page.url):
        _log("Session expired or missing — starting NYU SSO login.")
        mode = _normalize_login_mode(login_mode)
        if mode == "manual":
            _log("Manual login mode: please complete SSO + Duo in browser window.")
        elif mode == "auto":
            await _submit_sso_credentials(page, username=username, password=password)
            _log("Credentials submitted. Waiting for Duo MFA — complete it in the browser window.")
        else:
            try:
                await _submit_sso_credentials(page, username=username, password=password)
                _log("Credentials submitted. Waiting for Duo MFA — complete it in the browser window.")
            except Exception as exc:
                _log(f"Auto-login step failed, continue manual SSO: {exc}")
                _log("Hybrid login mode: please complete SSO + Duo in browser window.")

        success = await _wait_for_brightspace(page, base_url, timeout_s=180)
        if not success:
            _log("Timed out waiting for Duo — aborting.")
            await page.close()
            raise RuntimeError("Duo MFA timeout: user did not complete verification in time.")
        _log("Authentication successful.")
    else:
        _log("Session restored without re-login.")

    # Persist updated cookies
    updated = await context.cookies()
    save_session(updated)
    _log(f"Saved {len(updated)} cookies to disk.")
    await page.close()


# ── Scrapers ──────────────────────────────────────────────────────────────────

async def _scrape_courses(
    page,
    base_url: str,
    *,
    debug_enabled: bool = True,
    debug_dir: Path | None = None,
    discovery_timeout_ms: int = 25_000,
) -> tuple[list[dict[str, str]], dict[str, Any], list[str]]:
    """Return discovered courses and diagnostics."""
    attempted_urls: list[str] = []
    selectors_used: list[str] = []
    debug_artifacts: list[str] = []

    selector_profiles = [
        "a[href*='?ou='][aria-label], a[href*='?ou='][title]",
        "main a[href*='?ou='], [role='main'] a[href*='?ou=']",
        "a[href*='?ou=']",
        "a[href*='/d2l/home/']",
        "a[href*='ou=']",
    ]

    all_courses: list[dict[str, str]] = []
    for target_url in _candidate_course_urls(base_url):
        attempted_urls.append(target_url)
        _log(f"Scraping course list from {target_url} …")
        try:
            await page.goto(target_url, timeout=30_000)
            await page.wait_for_load_state("domcontentloaded", timeout=discovery_timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=discovery_timeout_ms)
            except Exception:
                pass
        except Exception as exc:
            _log(f"  Failed to load course discovery page {target_url}: {exc}")
            continue

        links_payload: list[dict[str, str]] = []
        for selector in selector_profiles:
            selectors_used.append(selector)
            anchors = await page.query_selector_all(selector)
            if not anchors:
                continue
            for anchor in anchors:
                href = await anchor.get_attribute("href") or ""
                text = (await anchor.inner_text()).strip()
                title = (await anchor.get_attribute("title") or "").strip()
                aria_label = (await anchor.get_attribute("aria-label") or "").strip()
                links_payload.append(
                    {
                        "href": href,
                        "text": text,
                        "title": title,
                        "aria_label": aria_label,
                    }
                )

        courses = _discover_courses_from_links(links_payload, base_url)
        if courses:
            all_courses = courses
            for course in courses:
                _log(f"  Found course: {course['name']!r} (ou={course['ou']})")
            break

        if debug_enabled and debug_dir is not None:
            debug_artifacts.extend(
                await _capture_debug_artifacts(page, debug_dir, stage="course_discovery_no_match")
            )

    discovery_meta = {
        "attempted_urls": attempted_urls,
        "selectors_used": selectors_used,
        "matched_links": len(all_courses),
    }
    _log(f"Total courses found: {len(all_courses)}")
    return all_courses, discovery_meta, debug_artifacts


async def _scrape_assignments(
    page, base_url: str, ou: str, course_name: str
) -> tuple[list[dict], list[dict[str, Any]]]:
    """Scrape assignments (dropbox) for one course."""
    url = f"{base_url}/d2l/lms/dropbox/dropbox.d2l?ou={ou}"
    _log(f"Scraping assignments for {course_name!r} at {url}")
    try:
        await page.goto(url, timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception as exc:
        _log(f"  Failed to load assignments page: {exc}")
        return [], []

    tasks: list[dict] = []
    assignment_refs: list[dict[str, Any]] = []
    rows = await page.query_selector_all("tr[class*='d_gd'], table.d2l-table tr")
    for row in rows:
        # Title cell
        title_el = await row.query_selector("a, td.d_gt, td[headers]")
        if not title_el:
            continue
        title = (await title_el.inner_text()).strip()
        if not title:
            continue
        link = await title_el.get_attribute("href") or ""
        if link and not link.startswith("http"):
            link = base_url + link

        # Due date — look for a cell that contains date-like text
        due_text = None
        cells = await row.query_selector_all("td")
        for cell in cells:
            cell_text = (await cell.inner_text()).strip()
            if any(m in cell_text for m in ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")):
                due_text = cell_text
                break

        task_id = _make_id("bspace_pw", title, link or None, due_text)
        tasks.append({
            "id": task_id,
            "title": title,
            "url": link or None,
            "due_at": _parse_due(due_text),
            "course": course_name,
            "source": "brightspace",
            "tags": ["brightspace", "assignment"],
            "status": "open",
            "priority": 0,
        })
        if link:
            assignment_refs.append(
                {
                    "course": course_name,
                    "title": title,
                    "url": link,
                    "due_at": _parse_due(due_text),
                }
            )
        _log(f"  Assignment: {title!r} due {due_text}")
    _log(f"  {len(tasks)} assignments scraped.")
    return tasks, assignment_refs


async def _scrape_calendar(page, base_url: str, ou: str, course_name: str) -> list[dict]:
    """Scrape calendar events for one course (month view)."""
    url = f"{base_url}/d2l/le/calendar/{ou}/view/month"
    _log(f"Scraping calendar for {course_name!r} at {url}")
    try:
        await page.goto(url, timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception as exc:
        _log(f"  Failed to load calendar page: {exc}")
        return []

    tasks: list[dict] = []
    # Calendar events rendered as list items or anchors with event titles
    event_els = await page.query_selector_all(
        ".d2l-calendar-event, [class*='cal-event'], a[class*='event']"
    )
    if not event_els:
        # Fallback: grab any anchor inside a day cell
        event_els = await page.query_selector_all("td.d_cal_day a, td[class*='calendar'] a")

    for el in event_els:
        title = (await el.inner_text()).strip()
        if not title:
            continue
        link = await el.get_attribute("href") or ""
        if link and not link.startswith("http"):
            link = base_url + link

        # Try to extract datetime from a nearby element or aria-label
        due_text = await el.get_attribute("aria-label") or await el.get_attribute("title") or None

        task_id = _make_id("bspace_pw", title, link or None, due_text)
        tasks.append({
            "id": task_id,
            "title": title,
            "url": link or None,
            "due_at": _parse_due(due_text),
            "course": course_name,
            "source": "brightspace",
            "tags": ["brightspace", "calendar"],
            "status": "open",
            "priority": 0,
        })
        _log(f"  Event: {title!r}")
    _log(f"  {len(tasks)} calendar events scraped.")
    return tasks


async def _scrape_grades(page, base_url: str, ou: str, course_name: str) -> list[dict]:
    """Scrape published grade items for one course."""
    url = f"{base_url}/d2l/lms/grades/my_grades/main.d2l?ou={ou}"
    _log(f"Scraping grades for {course_name!r} at {url}")
    try:
        await page.goto(url, timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception as exc:
        _log(f"  Failed to load grades page: {exc}")
        return []

    tasks: list[dict] = []
    rows = await page.query_selector_all("tr.d_gd, table.d2l-table tr, [class*='grade-item']")
    for row in rows:
        title_el = await row.query_selector("th, td.d_gt, [class*='grade-name']")
        if not title_el:
            continue
        title = (await title_el.inner_text()).strip()
        if not title:
            continue

        # Grade value
        grade_el = await row.query_selector("td.d_gr, [class*='grade-pts'], [class*='grade-value']")
        grade_text = ""
        if grade_el:
            grade_text = (await grade_el.inner_text()).strip()

        snippet = f"Grade: {grade_text}" if grade_text else None
        task_id = _make_id("bspace_pw", f"grade:{title}", None, None)
        tasks.append({
            "id": task_id,
            "title": f"[Grade] {title}",
            "url": url,
            "due_at": None,
            "course": course_name,
            "source": "brightspace",
            "tags": ["brightspace", "grade"],
            "snippet": snippet,
            "status": "open",
            "priority": 0,
        })
        _log(f"  Grade item: {title!r} — {grade_text}")
    _log(f"  {len(tasks)} grade items scraped.")
    return tasks


async def _collect_assignment_attachments(page, base_url: str, assignment_url: str) -> list[dict[str, str]]:
    """Collect downloadable attachment links from an assignment detail page."""
    try:
        await page.goto(assignment_url, timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception as exc:
        _log(f"  Failed to open assignment detail page: {exc}")
        return []

    found: list[dict[str, str]] = []
    seen: set[str] = set()
    candidate_selectors = [
        "a[download][href]",
        "a[href*='download'][href]",
        "a[href*='attachment'][href]",
        "[class*='attachment'] a[href]",
        "a[href]",
    ]
    for selector in candidate_selectors:
        anchors = await page.query_selector_all(selector)
        if not anchors:
            continue
        for anchor in anchors:
            href = await anchor.get_attribute("href")
            if not href:
                continue
            full_url = urljoin(base_url + "/", href)
            if not full_url.startswith(("http://", "https://")):
                continue
            text = (await anchor.inner_text()).strip()
            download_name = await anchor.get_attribute("download")
            fallback_name = Path(urlparse(full_url).path).name or "attachment"
            filename = (download_name or text or fallback_name).strip()
            if not is_supported_attachment(full_url, filename):
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            found.append({"url": full_url, "name": filename})
    return found


async def _download_attachment(context, url: str, dest: Path, retries: int = 2) -> None:
    """Download a single file with authenticated browser context."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await context.request.get(url, timeout=60_000)
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status} for {url}")
            body = await resp.body()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return
        except Exception as exc:  # pragma: no cover - network instability path
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(1.0 + attempt)
    raise RuntimeError(f"Download failed for {url}: {last_error}")


# ── Top-level entry point ─────────────────────────────────────────────────────

async def run_scraper(
    base_url: str,
    username: str,
    password: str,
    archive_root: Path | None = None,
    *,
    debug_enabled: bool = True,
    debug_dir: Path | None = None,
    discovery_timeout_ms: int = 25_000,
    login_mode: str = "manual",
) -> dict[str, Any]:
    """
    Full scrape pipeline.

    Returns::

        {
            "status": "success" | "error",
            "tasks": [<task dicts>],
            "message": "<human-readable summary>"
        }
    """
    try:
        from playwright.async_api import async_playwright  # local import — optional dep
    except ImportError:
        return {
            "status": "error",
            "tasks": [],
            "archives": [],
            "message": "playwright is not installed. Run: pip install playwright && playwright install chromium",
        }

    all_task_dicts: list[dict] = []
    archives: list[dict[str, str | None]] = []
    archive_failures: list[dict[str, str | None]] = []
    archive_root = archive_root or _archive_root_default()
    debug_dir = debug_dir or (Path(__file__).resolve().parents[3] / "tmp" / "debug" / "archive_sync")
    discovery_meta: dict[str, Any] = {
        "attempted_urls": [],
        "selectors_used": [],
        "matched_links": 0,
    }
    debug_artifacts: list[str] = []

    async with async_playwright() as pw:
        # headed (non-headless) so the user can see and complete Duo
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()

        try:
            await _login_or_restore_session(
                context,
                base_url,
                username,
                password,
                login_mode=login_mode,
            )

            page = await context.new_page()
            courses, discovery_meta, discovery_artifacts = await _scrape_courses(
                page,
                base_url,
                debug_enabled=debug_enabled,
                debug_dir=debug_dir,
                discovery_timeout_ms=discovery_timeout_ms,
            )
            debug_artifacts.extend(discovery_artifacts)
            await page.close()

            if not courses:
                _log("No courses found — check login or Brightspace course widget.")

            for course in courses:
                ou = course["ou"]
                name = course["name"]
                page = await context.new_page()
                assignment_tasks, assignment_refs = await _scrape_assignments(page, base_url, ou, name)
                all_task_dicts.extend(assignment_tasks)
                await page.close()

                # Download attachments for each assignment and archive them locally.
                for assignment in assignment_refs:
                    details_page = await context.new_page()
                    attachments = await _collect_assignment_attachments(
                        details_page,
                        base_url=base_url,
                        assignment_url=assignment["url"],
                    )
                    await details_page.close()
                    for attachment in attachments:
                        try:
                            archive_name = build_archive_filename(
                                course=assignment["course"],
                                title=assignment["title"],
                                due_at=assignment.get("due_at"),
                                source_url=attachment["url"],
                            )
                            course_dir = archive_root / sanitize_segment(assignment["course"])
                            archived_path = course_dir / archive_name
                            if not archived_path.exists():
                                await _download_attachment(context, attachment["url"], archived_path)
                            archives.append(
                                {
                                    "course": assignment["course"],
                                    "original_name": attachment["name"],
                                    "attachment_url": attachment["url"],
                                    "archived_path": str(archived_path.resolve()),
                                    "due_date": _due_date_iso(assignment.get("due_at")),
                                }
                            )
                        except Exception as exc:
                            archive_failures.append(
                                {
                                    "course": assignment["course"],
                                    "assignment_title": assignment["title"],
                                    "attachment_url": attachment["url"],
                                    "error": str(exc),
                                }
                            )
                            _log(
                                f"  Attachment download failed for {attachment['url']}: {exc}"
                            )

                page = await context.new_page()
                all_task_dicts.extend(await _scrape_calendar(page, base_url, ou, name))
                await page.close()

                page = await context.new_page()
                all_task_dicts.extend(await _scrape_grades(page, base_url, ou, name))
                await page.close()

        except Exception as exc:
            _log(f"Scraper error: {exc}")
            await browser.close()
            return {
                "status": "error",
                "tasks": [],
                "archives": [],
                "archive_failures": archive_failures,
                "course_discovery": discovery_meta,
                "debug_artifacts": debug_artifacts,
                "message": str(exc),
            }

        await browser.close()

    _log(f"Scraping complete. Total raw items: {len(all_task_dicts)}")
    return {
        "status": "success",
        "tasks": all_task_dicts,
        "archives": archives,
        "archive_failures": archive_failures,
        "course_discovery": discovery_meta,
        "debug_artifacts": debug_artifacts,
        "message": (
            f"Scraped {len(all_task_dicts)} items from {len(courses)} courses; "
            f"archived {len(archives)} files; {len(archive_failures)} failures."
        ),
    }
