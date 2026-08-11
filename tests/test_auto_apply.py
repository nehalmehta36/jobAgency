"""
tests/test_auto_apply.py — Test suite for the auto-apply feature.
Run: pytest tests/test_auto_apply.py -v
"""
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    import job_agent.db as db_mod
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    db_mod.init_db()
    yield conn
    conn.close()


@pytest.fixture()
def sample_job(db_conn):
    import job_agent.db as db_mod
    job = {
        "id": "job-abc123",
        "title": "Backend AI Engineer",
        "company": "TestCo",
        "location": "Delhi, India",
        "salary": "40-50 LPA",
        "portal": "naukri",
        "jd_url": "https://www.naukri.com/job-listings-backend-ai-engineer-testco-123",
        "jd_text": "Python FastAPI LangChain RAG",
        "relevance_score": 85,
        "relevance_reason": "Strong match",
        "scraped_at": db_mod.now_iso(),
        "status": "new",
    }
    db_mod.upsert_job(db_conn, job)
    db_conn.commit()
    return job


@pytest.fixture()
def sample_application(db_conn, sample_job, tmp_path):
    import job_agent.db as db_mod
    from datetime import date
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    app = {
        "id": "app-xyz789",
        "job_id": sample_job["id"],
        "company": sample_job["company"],
        "role": sample_job["title"],
        "portal": sample_job["portal"],
        "applied_date": date.today().strftime("%Y%m%d"),
        "status": "applied",
        "resume_file": str(pdf),
        "jd_url": sample_job["jd_url"],
        "last_email_update": None,
        "notes": "ATS passed",
        "created_at": db_mod.now_iso(),
        "apply_method": "manual",
    }
    db_mod.insert_application(db_conn, app)
    db_conn.commit()
    return app


# ── DB migration tests ────────────────────────────────────────────────────────

class TestDBMigration:

    def test_apply_method_column_exists(self, db_conn):
        cols = {r[1] for r in db_conn.execute("PRAGMA table_info(applications)").fetchall()}
        assert "apply_method" in cols

    def test_apply_method_default_is_manual(self, db_conn, sample_application):
        row = db_conn.execute(
            "SELECT apply_method FROM applications WHERE id=?", (sample_application["id"],)
        ).fetchone()
        assert row["apply_method"] == "manual"

    def test_init_db_is_idempotent(self, tmp_path, monkeypatch):
        import job_agent.db as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "idem.db")
        db_mod.init_db()
        db_mod.init_db()  # must not raise

    def test_get_application_by_job_returns_row(self, db_conn, sample_application):
        import job_agent.db as db_mod
        row = db_mod.get_application_by_job(db_conn, sample_application["job_id"])
        assert row is not None
        assert row["id"] == sample_application["id"]

    def test_get_application_by_job_returns_none_for_unknown(self, db_conn):
        import job_agent.db as db_mod
        assert db_mod.get_application_by_job(db_conn, "no-such-job") is None

    def test_update_application_sets_apply_method(self, db_conn, sample_application):
        import job_agent.db as db_mod
        db_mod.update_application(db_conn, sample_application["id"], apply_method="auto")
        db_conn.commit()
        row = db_conn.execute(
            "SELECT apply_method FROM applications WHERE id=?", (sample_application["id"],)
        ).fetchone()
        assert row["apply_method"] == "auto"


# ── ApplyResult + portal support tests ───────────────────────────────────────

class TestApplyResult:

    def test_dataclass_fields(self):
        from job_agent.applier import ApplyResult
        r = ApplyResult(success=True, method="auto", message="Applied!", jd_url="https://x.com")
        assert r.success is True and r.method == "auto"

    def test_to_dict_is_json_serialisable(self):
        from job_agent.applier import ApplyResult
        d = ApplyResult(True, "auto", "ok", "https://x.com").to_dict()
        json.dumps(d)  # must not raise

    def test_portal_support_keys(self):
        from job_agent.applier import PORTAL_APPLY_SUPPORT
        assert set(PORTAL_APPLY_SUPPORT.keys()) == {"naukri", "linkedin", "indeed", "ziprecruiter", "dice"}

    def test_naukri_is_full(self):
        from job_agent.applier import PORTAL_APPLY_SUPPORT
        assert PORTAL_APPLY_SUPPORT["naukri"] == "full"

    def test_linkedin_indeed_are_conditional(self):
        from job_agent.applier import PORTAL_APPLY_SUPPORT
        assert PORTAL_APPLY_SUPPORT["linkedin"] == "conditional"
        assert PORTAL_APPLY_SUPPORT["indeed"] == "conditional"

    def test_ziprecruiter_dice_are_none(self):
        from job_agent.applier import PORTAL_APPLY_SUPPORT
        assert PORTAL_APPLY_SUPPORT["ziprecruiter"] == "none"
        assert PORTAL_APPLY_SUPPORT["dice"] == "none"


class TestSessionHelpers:

    def test_session_path_format(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        assert m._session_path("naukri") == tmp_path / "naukri" / "state.json"

    def test_session_exists_false_when_missing(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        assert m._session_exists("naukri") is False

    def test_session_exists_true_when_present(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        p = m._session_path("naukri")
        p.parent.mkdir(parents=True)
        p.write_text('{"cookies":[]}')
        assert m._session_exists("naukri") is True

    def test_save_session_writes_file(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        ctx = MagicMock()
        ctx.storage_state.return_value = {"cookies": [], "origins": []}
        m._save_session(ctx, "linkedin")
        assert m._session_path("linkedin").is_file()

    def test_load_session_kwargs_empty_when_no_file(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        assert m._load_session_kwargs("naukri") == {}

    def test_load_session_kwargs_returns_path_when_exists(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        p = m._session_path("naukri")
        p.parent.mkdir(parents=True)
        p.write_text('{"cookies":[]}')
        kwargs = m._load_session_kwargs("naukri")
        assert "storage_state" in kwargs


class TestTryAutoApplyDispatcher:

    def _job(self, portal: str, jd_url: str = "https://example.com/job") -> dict:
        return {"id": "j1", "title": "Engineer", "company": "Co", "portal": portal, "jd_url": jd_url}

    def test_unsupported_portals_return_unsupported(self, tmp_path):
        from job_agent.applier import try_auto_apply
        pdf = tmp_path / "r.pdf"; pdf.write_bytes(b"fake")
        for p in ("ziprecruiter", "dice"):
            r = try_auto_apply(self._job(p), str(pdf))
            assert r.success is False and r.method == "unsupported"

    def test_missing_resume_returns_manual_fallback(self):
        from job_agent.applier import try_auto_apply
        r = try_auto_apply(self._job("naukri"), "/no/such/file.pdf")
        assert r.success is False and r.method == "manual_fallback"
        assert "not found" in r.message.lower()

    def test_playwright_launched_headless_for_supported_portal(self, tmp_path, monkeypatch):
        import job_agent.applier as m
        monkeypatch.setattr(m, "SESSIONS_DIR", tmp_path)
        pdf = tmp_path / "r.pdf"; pdf.write_bytes(b"fake")

        mock_page = MagicMock()
        mock_page.inner_text.return_value = "application submitted"
        mock_page.locator.return_value.first.is_visible.return_value = True
        mock_ctx = MagicMock()
        mock_ctx.new_page.return_value = mock_page
        mock_ctx.storage_state.return_value = {"cookies": [], "origins": []}
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_ctx
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        with patch("job_agent.applier.sync_playwright") as sp:
            sp.return_value.__enter__.return_value = mock_pw
            sp.return_value.__exit__.return_value = False
            m.try_auto_apply(self._job("naukri"), str(pdf))

        mock_pw.chromium.launch.assert_called_once_with(headless=True)


class TestApplyJobTool:

    def _patch_db(self, monkeypatch, db_conn):
        import job_agent.db as db_mod
        @contextmanager
        def _fake():
            yield db_conn
        monkeypatch.setattr(db_mod, "get_db", _fake)
        monkeypatch.setattr(db_mod, "init_db", lambda: None)

    def test_apply_job_in_all_tools(self):
        from job_agent.tools import ALL_TOOLS
        assert "apply_job" in [t.name for t in ALL_TOOLS]

    def test_apply_job_job_not_found(self, db_conn, monkeypatch):
        import job_agent.tools as tools
        self._patch_db(monkeypatch, db_conn)
        r = tools.apply_job.invoke({"job_id": "nonexistent"})
        assert "not found" in r.lower()

    def test_apply_job_unsupported_portal(self, db_conn, sample_job, monkeypatch):
        import job_agent.db as db_mod, job_agent.tools as tools
        db_conn.execute("UPDATE jobs SET portal='dice' WHERE id=?", (sample_job["id"],))
        db_conn.commit()
        self._patch_db(monkeypatch, db_conn)
        r = tools.apply_job.invoke({"job_id": sample_job["id"]})
        assert "not supported" in r.lower() or "manually" in r.lower()

    def test_apply_job_no_application_row(self, db_conn, sample_job, monkeypatch):
        import job_agent.tools as tools
        self._patch_db(monkeypatch, db_conn)
        r = tools.apply_job.invoke({"job_id": sample_job["id"]})
        assert "tailor" in r.lower() or "not found" in r.lower()

    def test_apply_job_success_updates_db(self, db_conn, sample_job, sample_application, monkeypatch):
        import job_agent.tools as tools
        from job_agent.applier import ApplyResult
        self._patch_db(monkeypatch, db_conn)
        mock_r = ApplyResult(True, "auto", "Done.", sample_job["jd_url"])
        with patch.object(tools, "try_auto_apply", return_value=mock_r):
            tools.apply_job.invoke({"job_id": sample_job["id"]})
        row = db_conn.execute(
            "SELECT apply_method FROM applications WHERE id=?", (sample_application["id"],)
        ).fetchone()
        assert row["apply_method"] == "auto"

    def test_apply_job_failure_returns_manual_url(self, db_conn, sample_job, sample_application, monkeypatch):
        import job_agent.tools as tools
        from job_agent.applier import ApplyResult
        self._patch_db(monkeypatch, db_conn)
        mock_r = ApplyResult(False, "manual_fallback", "Session expired.", sample_job["jd_url"])
        with patch.object(tools, "try_auto_apply", return_value=mock_r):
            r = tools.apply_job.invoke({"job_id": sample_job["id"]})
        assert "manually" in r.lower() or "naukri.com" in r.lower()
