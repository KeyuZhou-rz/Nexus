"""Theme-aware styles for the single-page task workspace."""

DASHBOARD_CSS = """
<style>
:root {
    --nx-bg: #f4f1e9;
    --nx-panel: #fffdf8;
    --nx-text: #202c25;
    --nx-muted: #5b6a60;
    --nx-line: #dbdfd5;
    --nx-accent: #226f54;
    --nx-on-accent: #ffffff;
    --nx-soft: #e6eee6;
}
@media (prefers-color-scheme: dark) {
    :root {
        --nx-bg: #191f1c;
        --nx-panel: #252d27;
        --nx-text: #e5ece6;
        --nx-muted: #a4b2a8;
        --nx-line: #3d493f;
        --nx-accent: #92cbb0;
        --nx-on-accent: #172a20;
        --nx-soft: #344b3c;
    }
}
[data-testid="stApp"], [data-testid="stAppViewContainer"] {
    background: var(--nx-bg);
    color: var(--nx-text);
    font-family: system-ui, "Noto Sans SC", sans-serif;
}
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1080px; padding: 2rem 2rem 3rem; }
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] :is(h1, h2, h3, h4, h5, h6),
[data-testid="stWidgetLabel"], [data-testid="stExpander"] summary {
    color: var(--nx-text);
}
[data-testid="stCaptionContainer"] { color: var(--nx-muted); }
[data-testid="stMarkdownContainer"] h1 { font-size: 24px; font-weight: 500; }
[data-testid="stMarkdownContainer"] h2 { font-size: 18px; font-weight: 500; }
[data-testid="stMarkdownContainer"] .focus-title { font-size: 27px; line-height: 1.35; margin: 14px 0 8px; overflow-wrap: anywhere; }
[data-testid="stMarkdownContainer"] .focus-kicker { color: var(--nx-accent); margin: 0; font-size: 13px; }
.st-key-nexus-header { border-bottom: 1px solid var(--nx-line); padding-bottom: 18px; margin-bottom: 22px; }
.st-key-current-task { background: var(--nx-panel); padding: 26px; border-radius: 16px; min-height: 290px; }
.st-key-nexus-workspace [data-testid="stColumn"]:has(.st-key-current-task) { position: sticky; top: 1.5rem; align-self: flex-start; }
.st-key-custom-task { background: var(--nx-panel); padding: 24px; border-radius: 14px; margin-top: 22px; }
button[kind^="secondary"] { background: var(--nx-panel); border-color: var(--nx-line); color: var(--nx-text); border-radius: 8px; }
button[kind^="primary"] { background: var(--nx-accent); border-color: var(--nx-accent); color: var(--nx-on-accent); border-radius: 8px; }
button[kind^="primary"]:is(:hover, :active, :focus-visible) { background: var(--nx-accent); border-color: var(--nx-accent); color: var(--nx-on-accent); }
button[kind^="secondary"]:is(:hover, :active, :focus-visible) { background: var(--nx-soft); border-color: var(--nx-accent); color: var(--nx-text); }
button[kind^="primary"] [data-testid="stMarkdownContainer"] { color: var(--nx-on-accent); }
.st-key-task-list [data-testid="stButton"] button { display: block; text-align: left; padding: 14px 16px; border-radius: 12px; }
.st-key-task-list [data-testid="stButton"] button > div { justify-content: flex-start; width: 100%; }
.st-key-task-list [data-testid="stButton"] button [data-testid="stMarkdownContainer"] { text-align: left; width: 100%; }
.st-key-task-list [data-testid="stButton"] button p { margin: 0; font-weight: 500; }
.st-key-task-list [data-testid="stButton"] button p + p { margin-top: 5px; font-size: 12px; font-weight: 400; color: var(--nx-muted); }
.st-key-task-list button[kind="primary"] { background: var(--nx-soft); border-color: var(--nx-accent); }
.st-key-task-list button[kind="primary"]:is(:hover, :active, :focus-visible) { background: var(--nx-soft); color: var(--nx-text); }
.st-key-task-list button[kind="primary"] [data-testid="stMarkdownContainer"] { color: var(--nx-text); }
[data-testid="stForm"], [data-testid="stExpander"] { border-color: var(--nx-line); }
[data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"],
[data-testid="stDateInput"] input, [data-testid="stTimeInput"] input {
    background: var(--nx-bg); color: var(--nx-text);
}
input { color: var(--nx-text) !important; }
input::placeholder { color: var(--nx-muted) !important; }
#MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; height: 0; }
@media (max-width: 650px) {
    .block-container { padding: 1.25rem 1rem 2rem; }
    .st-key-current-task, .st-key-custom-task { padding: 20px; min-height: 0; }
    .st-key-nexus-workspace [data-testid="stColumn"]:has(.st-key-current-task) { position: static; }
}
</style>
"""
