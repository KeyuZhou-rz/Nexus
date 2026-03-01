from __future__ import annotations

from nexus.archive_sync.scraper import (
    _candidate_course_urls,
    _clean_course_title,
    _discover_courses_from_links,
    _extract_ou_from_href,
)


def test_candidate_course_urls_contains_fallbacks():
    urls = _candidate_course_urls("https://example.brightspace.com/")
    assert urls == [
        "https://example.brightspace.com/d2l/home",
        "https://example.brightspace.com/d2l/home?isCourseNav=1",
        "https://example.brightspace.com/d2l/le/manageCourses/main.d2l",
    ]


def test_extract_ou_from_href_supports_query_and_path_forms():
    assert _extract_ou_from_href("https://x.com/d2l/home?ou=12345") == "12345"
    assert _extract_ou_from_href("https://x.com/d2l/home/67890") == "67890"
    assert _extract_ou_from_href("https://x.com/d2l/lms/dropbox/dropbox.d2l?foo=1&ou=2468") == "2468"
    assert _extract_ou_from_href("https://x.com/d2l/home") is None


def test_clean_course_title_removes_brightspace_suffix():
    assert _clean_course_title(" General Physics II | Brightspace ") == "General Physics II"
    assert _clean_course_title("OS Lab") == "OS Lab"


def test_discover_courses_from_links_dedupes_and_filters_noise():
    links = [
        {
            "href": "/d2l/home?ou=1001",
            "text": "General Physics II",
            "title": "",
            "aria_label": "",
        },
        {
            "href": "/d2l/home/1002",
            "text": "Multivariable Calculus | Brightspace",
            "title": "",
            "aria_label": "",
        },
        {
            "href": "/d2l/home?ou=1001",
            "text": "General Physics II",
            "title": "",
            "aria_label": "",
        },
        {
            "href": "/d2l/home?ou=2001",
            "text": "Home",
            "title": "",
            "aria_label": "",
        },
    ]

    courses = _discover_courses_from_links(links, "https://example.brightspace.com")

    assert courses == [
        {"name": "General Physics II", "ou": "1001"},
        {"name": "Multivariable Calculus", "ou": "1002"},
    ]
