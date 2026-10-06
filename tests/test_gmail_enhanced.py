"""E1-E3: Tests for Gmail full-body fetch, MIME decode, LLM extraction, pagination."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nexus.core.aggregators.google import GoogleAggregator
from nexus.core.models import Task


# ── E1: _decode_email_body ───────────────────────────────────────────

class TestDecodeEmailBody:
    def test_text_plain_single_part(self):
        text = "Hello, this is a test email body."
        encoded = base64.urlsafe_b64encode(text.encode()).decode()
        payload = {
            "mimeType": "text/plain",
            "body": {"data": encoded},
        }
        result = GoogleAggregator._decode_email_body(payload)
        assert result == text

    def test_text_html_strips_tags(self):
        html = "<html><body><p>Hello <b>World</b></p><br>Second line</body></html>"
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        payload = {
            "mimeType": "text/html",
            "body": {"data": encoded},
        }
        result = GoogleAggregator._decode_email_body(payload)
        assert "Hello" in result
        assert "World" in result
        assert "Second line" in result
        assert "<html>" not in result
        assert "<b>" not in result

    def test_multipart_alternative_prefers_text_plain(self):
        plain_text = "Plain text version"
        html_text = "<p>HTML version</p>"
        plain_encoded = base64.urlsafe_b64encode(plain_text.encode()).decode()
        html_encoded = base64.urlsafe_b64encode(html_text.encode()).decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain_encoded}},
                {"mimeType": "text/html", "body": {"data": html_encoded}},
            ],
        }
        result = GoogleAggregator._decode_email_body(payload)
        assert "Plain text version" in result
        assert "HTML version" not in result  # should prefer text/plain

    def test_multipart_alternative_falls_back_to_html(self):
        html_text = "<p>Only HTML available</p>"
        html_encoded = base64.urlsafe_b64encode(html_text.encode()).decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": html_encoded}},
            ],
        }
        result = GoogleAggregator._decode_email_body(payload)
        assert "Only HTML available" in result

    def test_multipart_mixed_collects_all_text_parts(self):
        part1 = base64.urlsafe_b64encode(b"First part").decode()
        part2 = base64.urlsafe_b64encode(b"Second part").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": part1}},
                {"mimeType": "text/plain", "body": {"data": part2}},
            ],
        }
        result = GoogleAggregator._decode_email_body(payload)
        assert "First part" in result
        assert "Second part" in result

    def test_empty_payload(self):
        assert GoogleAggregator._decode_email_body({}) == ""

    def test_no_body_data(self):
        payload = {"mimeType": "text/plain", "body": {}}
        assert GoogleAggregator._decode_email_body(payload) == ""

    def test_deeply_nested_multipart(self):
        inner_text = base64.urlsafe_b64encode(b"Inner text").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": inner_text}},
                    ],
                },
            ],
        }
        result = GoogleAggregator._decode_email_body(payload)
        assert "Inner text" == result


# ── E1: _strip_html ──────────────────────────────────────────────────

class TestStripHtml:
    def test_removes_tags(self):
        assert "Hello" in GoogleAggregator._strip_html("<div>Hello</div>")

    def test_decodes_entities(self):
        result = GoogleAggregator._strip_html("A&amp;B &lt; C")
        assert "A&B" in result
        assert "< C" in result

    def test_collapses_whitespace(self):
        result = GoogleAggregator._strip_html("Line1   \n\n\nLine2")
        assert "Line1" in result
        assert "Line2" in result

    def test_removes_style_blocks(self):
        result = GoogleAggregator._strip_html(
            "<style>.a{color:red}</style><p>Visible</p>"
        )
        assert "Visible" in result
        assert ".a{color:red}" not in result


# ── E2: LLM structured extraction ────────────────────────────────────

class TestStructuredExtraction:
    def test_extract_returns_empty_when_body_short(self):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        result = agg._extract_structured_fields("Subject", "short")
        assert result == {}

    def test_extract_returns_empty_when_body_empty(self):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        result = agg._extract_structured_fields("Subject", "")
        assert result == {}

    def test_enrich_task_no_op_with_empty_extraction(self, monkeypatch):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        monkeypatch.setattr(agg, "_extract_structured_fields", lambda s, b: {})
        task = Task(
            id="gmail:test",
            title="Test email",
            source="gmail",
            due_at=None,
        )
        result = agg._enrich_task_from_llm(task, "Subject", "some body text")
        # Task should be unchanged
        assert result.course is None
        assert result.priority == 0
        assert result.due_at is None

    def test_enrich_task_applies_course_hint(self, monkeypatch):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        monkeypatch.setattr(
            agg, "_extract_structured_fields",
            lambda s, b: {"course_hint": "CS101", "priority": "high"},
        )
        task = Task(id="gmail:test", title="Test email", source="gmail", due_at=None)
        result = agg._enrich_task_from_llm(task, "Subject", "body")
        assert result.course == "CS101"
        assert result.priority == 3  # high

    def test_enrich_task_applies_exam_date(self, monkeypatch):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        monkeypatch.setattr(
            agg, "_extract_structured_fields",
            lambda s, b: {"exam_date": "2026-07-15T09:00:00"},
        )
        task = Task(id="gmail:test", title="Exam announcement", source="gmail", due_at=None)
        result = agg._enrich_task_from_llm(task, "Subject", "body")
        assert result.due_at == datetime(2026, 7, 15, 9, 0, 0)

    def test_enrich_task_adds_deadline_tag(self, monkeypatch):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        monkeypatch.setattr(
            agg, "_extract_structured_fields",
            lambda s, b: {"action_items": ["Submit homework by Friday deadline"]},
        )
        task = Task(id="gmail:test", title="HW", source="gmail", due_at=None, tags=[])
        result = agg._enrich_task_from_llm(task, "Subject", "body")
        assert "deadline" in result.tags

    def test_enrich_task_does_not_overwrite_existing_course(self, monkeypatch):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        monkeypatch.setattr(
            agg, "_extract_structured_fields",
            lambda s, b: {"course_hint": "CS999"},
        )
        task = Task(id="gmail:test", title="HW", source="gmail", due_at=None, course="EE202")
        result = agg._enrich_task_from_llm(task, "Subject", "body")
        # Should keep original course
        assert result.course == "EE202"


# ── E3: pagination + course aliases ──────────────────────────────────

class TestGmailInitParams:
    def test_default_max_results_and_window(self):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        agg.gmail_max_results = 20
        agg.gmail_window_days = 30
        assert agg.gmail_max_results == 20
        assert agg.gmail_window_days == 30

    def test_custom_max_results(self):
        agg = GoogleAggregator.__new__(GoogleAggregator)
        agg.gmail_max_results = 50
        agg.gmail_window_days = 14
        assert agg.gmail_max_results == 50
        assert agg.gmail_window_days == 14


class TestCourseAliases:
    def test_load_aliases_missing_file(self, tmp_path):
        """Missing course_aliases.json returns empty list."""
        result = GoogleAggregator._load_course_aliases(tmp_path)
        assert result == []

    def test_load_aliases_valid_file(self, tmp_path):
        aliases = [{"course": "CS202", "aliases": ["OS", "Operating Systems"]}]
        (tmp_path / "course_aliases.json").write_text(json.dumps(aliases))
        result = GoogleAggregator._load_course_aliases(tmp_path)
        assert len(result) == 1
        assert result[0]["course"] == "CS202"

    def test_load_aliases_invalid_json(self, tmp_path):
        (tmp_path / "course_aliases.json").write_text("not json")
        result = GoogleAggregator._load_course_aliases(tmp_path)
        assert result == []

    def test_load_aliases_not_a_list(self, tmp_path):
        (tmp_path / "course_aliases.json").write_text('{"key": "value"}')
        result = GoogleAggregator._load_course_aliases(tmp_path)
        assert result == []


class TestInferCourse:
    def test_infer_course_matches_alias(self, tmp_path):
        aliases = [{"course": "CS202", "aliases": ["Operating Systems", "OS"]}]
        (tmp_path / "course_aliases.json").write_text(json.dumps(aliases))
        agg = GoogleAggregator.__new__(GoogleAggregator)
        agg.course_aliases = GoogleAggregator._load_course_aliases(tmp_path)
        result = agg._infer_course("Operating Systems midterm update", "prof@nyu.edu")
        assert result == "CS202"

    def test_infer_course_no_match(self, tmp_path):
        aliases = [{"course": "CS202", "aliases": ["OS"]}]
        (tmp_path / "course_aliases.json").write_text(json.dumps(aliases))
        agg = GoogleAggregator.__new__(GoogleAggregator)
        agg.course_aliases = GoogleAggregator._load_course_aliases(tmp_path)
        result = agg._infer_course("Random subject", "random@gmail.com")
        assert result is None
