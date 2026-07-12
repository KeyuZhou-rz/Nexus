PYTHONPATH := src
PYTHON     := PYTHONPATH=$(PYTHONPATH) python
PYTEST     := PYTHONPATH=$(PYTHONPATH) python -m pytest

COURSE_ID    ?= CS202_OS
INPUT        ?= data/testDataFile
DB_DIR       ?= data/chroma
SQLITE_PATH  ?= data/nexus.db

# ─── Dev / Env ────────────────────────────────────────────────────────────────

.PHONY: ui dashboard doctor

ui:
	PYTHONPATH=$(PYTHONPATH) streamlit run src/nexus/streamlit_app.py

dashboard:
	PYTHONPATH=$(PYTHONPATH) streamlit run src/nexus/dashboard_app.py

doctor:
	$(PYTHON) -m nexus.cli.doctor

# ─── Knowledge: Ingest ────────────────────────────────────────────────────────

.PHONY: ingest ingest-force ingest-legacy

ingest:
	$(PYTHON) -m nexus.cli.ingest \
		--input $(INPUT) \
		--course-id $(COURSE_ID) \
		--db-dir $(DB_DIR) \
		--sqlite-path $(SQLITE_PATH)

ingest-force:
	$(PYTHON) -m nexus.cli.ingest \
		--input $(INPUT) \
		--course-id $(COURSE_ID) \
		--db-dir $(DB_DIR) \
		--sqlite-path $(SQLITE_PATH) \
		--force

ingest-legacy:
	$(PYTHON) -m nexus.cli.ingest \
		--input $(INPUT) \
		--course-id $(COURSE_ID) \
		--db-dir $(DB_DIR) \
		--sqlite-path $(SQLITE_PATH) \
		--legacy

# ─── Knowledge: Query ─────────────────────────────────────────────────────────

.PHONY: query

QUERY ?= What is a process context switch?

query:
	$(PYTHON) -m nexus.cli.query \
		--query "$(QUERY)" \
		--course-id $(COURSE_ID) \
		--db-dir $(DB_DIR)

# ─── Task Pipeline & Briefing ─────────────────────────────────────────────────

.PHONY: pipeline pipeline-fast briefing

pipeline:
	$(PYTHON) -m nexus.cli.pipeline

pipeline-fast:
	$(PYTHON) -m nexus.cli.pipeline --skip-archive-sync --no-google

briefing:
	$(PYTHON) -m nexus.cli.briefing

briefing-llm:
	$(PYTHON) -m nexus.cli.briefing --llm --aggregate

# ─── Tests ────────────────────────────────────────────────────────────────────

.PHONY: test test-knowledge test-archive test-fast

test:
	$(PYTEST) tests/ -q

test-knowledge:
	$(PYTEST) tests/test_knowledge_chunking.py \
	          tests/test_knowledge_ingest.py \
	          tests/test_knowledge_ingest_files.py \
	          tests/test_knowledge_query.py \
	          -v

test-cli:
	$(PYTEST) tests/test_cli_smoke.py -v

test-archive:
	$(PYTEST) tests/test_archive_sync.py \
	          tests/test_archive_utils.py \
	          tests/test_archive_reporting.py \
	          -v 2>/dev/null || \
	$(PYTEST) tests/ -k "archive" -v

test-fast:
	$(PYTEST) tests/ -q --ignore=tests/test_archive_post_ingest.py

# ─── DB Inspection ────────────────────────────────────────────────────────────

.PHONY: db-stats db-reset

db-stats:
	@echo "=== SQLite ==="
	@sqlite3 $(SQLITE_PATH) \
		"SELECT 'documents', COUNT(*) FROM documents UNION ALL \
		 SELECT 'chunks',    COUNT(*) FROM chunks    UNION ALL \
		 SELECT 'interactions', COUNT(*) FROM interaction_logs;" 2>/dev/null \
	 || echo "(nexus.db not initialized)"
	@echo "=== ChromaDB collections ==="
	@$(PYTHON) -c "\
import chromadb; \
c = chromadb.PersistentClient('$(DB_DIR)'); \
[print(f'  {col.name}: {col.count()} chunks') for col in c.list_collections()]" \
	2>/dev/null || echo "(chroma not initialized)"

db-reset:
	@echo "WARNING: This will delete all ingested data."
	@read -p "Continue? [y/N] " ans; [ "$$ans" = "y" ] || exit 0
	rm -rf $(DB_DIR) $(SQLITE_PATH)
	@echo "DB reset complete."
