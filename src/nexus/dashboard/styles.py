from __future__ import annotations

DASHBOARD_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;700&display=swap');

:root {
    --bg: #f3efe6;
    --panel: rgba(255, 251, 243, 0.86);
    --panel-strong: #fffaf0;
    --text: #1d221d;
    --muted: rgba(29, 34, 29, 0.62);
    --border: rgba(29, 34, 29, 0.1);
    --accent: #226f54;
    --accent-soft: rgba(34, 111, 84, 0.12);
    --danger: #ba3d2f;
    --danger-soft: rgba(186, 61, 47, 0.12);
    --warn: #bf7b16;
    --warn-soft: rgba(191, 123, 22, 0.12);
    --info: #2d5baf;
    --info-soft: rgba(45, 91, 175, 0.12);
}

html, body, [data-testid="stAppViewContainer"] {
    background:
        radial-gradient(900px 540px at 0% 0%, rgba(34, 111, 84, 0.10), transparent 55%),
        radial-gradient(820px 460px at 100% 0%, rgba(191, 123, 22, 0.10), transparent 55%),
        linear-gradient(180deg, #f8f4ec 0%, var(--bg) 100%);
    color: var(--text);
}

[data-testid="stHeader"] {
    background: transparent;
}

[data-testid="stSidebar"] {
    background: rgba(255, 251, 243, 0.55);
    border-right: 1px solid var(--border);
}

* {
    font-family: "Noto Sans SC", "Space Grotesk", sans-serif;
}

.block-container {
    padding-top: 2rem;
    padding-left: 2rem;
    padding-right: 2rem;
    max-width: 1180px;
}

.briefing {
    background: var(--panel-strong);
    border: 1px solid var(--border);
    border-radius: 22px;
    padding: 28px 30px 22px;
    box-shadow: 0 16px 40px rgba(74, 57, 32, 0.06);
    font-size: 18px;
    line-height: 1.65;
}

.briefing p { margin: 0 0 1.1em; }
.briefing ul { margin: 0 0 1.2em; padding-left: 1.2em; }
.briefing li { margin: 0.25em 0; }
.briefing-kicker {
    font-size: 12px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 1.2em !important;
}
.briefing .headline {
    font-size: 28px;
    font-weight: 700;
    line-height: 1.3;
    margin-bottom: 1.1em !important;
}
.briefing .suggestion { font-weight: 600; }
.briefing-muted { color: var(--muted); }

.week-block {
    background: rgba(255, 255, 255, 0.56);
    border: 1px solid var(--border);
    border-left: 5px solid var(--accent);
    border-radius: 16px;
    padding: 14px 16px 10px;
    margin: 12px 0 4px;
}
.week-block.recommended {
    background: var(--accent-soft);
    border-left-color: var(--accent);
}
.week-block-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
}
.week-block-title {
    font-size: 16px;
    font-weight: 700;
    line-height: 1.35;
}
.week-block-badge {
    flex: 0 0 auto;
    font-size: 12px;
    font-weight: 700;
    color: var(--accent);
}
.week-block-meta {
    font-size: 13px;
    color: var(--muted);
    margin-top: 6px;
}

.timer-panel {
    margin: 16px 0 8px;
}

.hero {
    background: linear-gradient(135deg, rgba(255, 250, 240, 0.92), rgba(247, 240, 226, 0.82));
    border: 1px solid var(--border);
    border-radius: 24px;
    padding: 24px 26px;
    margin-bottom: 20px;
    box-shadow: 0 18px 50px rgba(74, 57, 32, 0.08);
}

.eyebrow {
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
}

.hero-title {
    font-size: 34px;
    font-weight: 700;
    line-height: 1.05;
    margin: 0;
}

.hero-sub {
    margin-top: 8px;
    font-size: 15px;
    color: var(--muted);
}

.chip-row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 16px;
}

.chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: rgba(255, 255, 255, 0.55);
    font-size: 13px;
}

.section-title {
    font-size: 22px;
    font-weight: 700;
    margin: 20px 0 12px;
}

.panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 16px 18px;
    box-shadow: 0 12px 36px rgba(74, 57, 32, 0.05);
}

.panel + .panel {
    margin-top: 14px;
}

.panel-kicker {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin-bottom: 8px;
}

.judgment-card {
    border-left: 6px solid var(--accent);
    background: var(--panel-strong);
    box-shadow: 0 16px 40px rgba(34, 111, 84, 0.10);
}

.task-card {
    background: rgba(255, 255, 255, 0.56);
    border: 1px solid var(--border);
    border-left: 5px solid var(--accent);
    border-radius: 16px;
    padding: 14px 16px;
    margin-bottom: 12px;
}

.task-card.overdue { border-left-color: var(--danger); background: var(--danger-soft); }
.task-card.today { border-left-color: var(--warn); background: var(--warn-soft); }
.task-card.upcoming { border-left-color: var(--accent); background: var(--accent-soft); }
.task-card.update { border-left-color: var(--info); background: var(--info-soft); }

.learning-card {
    background: rgba(255, 255, 255, 0.64);
    border: 1px solid var(--border);
    border-left: 5px solid var(--danger);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 12px;
}

.learning-card-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
}

.learning-title {
    font-size: 17px;
    font-weight: 700;
    line-height: 1.35;
}

.urgency-tag {
    flex: 0 0 auto;
    color: var(--danger);
    font-size: 12px;
    font-weight: 700;
}

.focus-action {
    margin-top: 10px;
    font-size: 14px;
    font-weight: 600;
}

.focus-action.muted,
.evidence-line {
    color: var(--muted);
}

.evidence-line {
    margin-top: 8px;
    font-family: "IBM Plex Mono", monospace;
    font-size: 11px;
}

.task-title {
    font-size: 17px;
    font-weight: 700;
    line-height: 1.35;
}

.task-meta {
    font-size: 13px;
    color: var(--muted);
    margin-top: 6px;
}

.task-snippet {
    font-size: 13px;
    color: var(--text);
    margin-top: 8px;
}

.task-link {
    display: inline-block;
    margin-top: 10px;
    font-size: 13px;
    color: var(--accent);
    text-decoration: none;
    font-weight: 600;
}

.group-label {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin: 18px 0 8px;
}

.status-good, .status-warn, .status-bad {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 11px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
}

.status-good { background: var(--accent-soft); color: var(--accent); }
.status-warn { background: var(--warn-soft); color: var(--warn); }
.status-bad { background: var(--danger-soft); color: var(--danger); }

.mini-note {
    font-size: 13px;
    color: var(--muted);
}

div[data-testid="stMetric"] {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 8px 10px;
}

div[data-testid="stAlert"] {
    border-radius: 16px;
}

div[data-testid="stDataFrame"] {
    border: 1px solid var(--border);
    border-radius: 18px;
    overflow: hidden;
}

@media (max-width: 768px) {
    .block-container {
        padding-left: 1rem;
        padding-right: 1rem;
    }

    .hero-title {
        font-size: 28px;
    }
}
</style>
"""
