"""
End-to-end tests for the job discovery pipeline.

Uses mock data — no external APIs or network calls.
Run: python -m pytest tests/test_pipeline.py -v
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Job
from core.database import get_connection, save_job, job_exists, job_exists_by_title_company, log_application
from core.enricher import requires_german, extract_apply_email
from core.ats_detector import detect_ats
from core.rate_limiter import remaining_applications_today
from delivery.cover_letter import _format_full_letter
from delivery.email import build_digest, send_digest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_job(**overrides) -> Job:
    """Create a Job with sensible defaults; override any field."""
    defaults = dict(
        title="Working Student Finance",
        company="TestCorp",
        location="Berlin, Germany",
        description="We are looking for a working student to support our FP&A team.",
        url="https://example.com/jobs/12345",
        source="test",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with the same schema as production."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            source TEXT, title TEXT, company TEXT, location TEXT,
            description TEXT, url TEXT UNIQUE,
            score TEXT, fit_score INTEGER DEFAULT 0, score_reason TEXT,
            cover_letter TEXT, found_date TEXT,
            status TEXT DEFAULT 'new', apply_email TEXT DEFAULT '',
            ats_platform TEXT DEFAULT '', ats_job_id TEXT DEFAULT '',
            ats_board_token TEXT DEFAULT '',
            apply_method TEXT DEFAULT '', apply_attempts INTEGER DEFAULT 0,
            apply_error TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id),
            method TEXT, status TEXT, submitted_at TEXT,
            error_message TEXT, response_data TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# German language filter tests
# ---------------------------------------------------------------------------

class TestGermanFilter:
    def test_explicit_german_required(self):
        assert requires_german("German language skills required")[0] is True

    def test_fluent_german(self):
        assert requires_german("Fluent in German B2")[0] is True

    def test_deutschkenntnisse(self):
        assert requires_german("Sehr gute Deutschkenntnisse erforderlich")[0] is True

    def test_english_only_passes(self):
        assert requires_german("English fluency required, team language is English")[0] is False

    def test_no_language_mention_passes(self):
        assert requires_german("Working student finance support FP&A team")[0] is False

    def test_german_in_title(self):
        assert requires_german("Finanzbuchhalter Werkstudent — fließend Deutsch")[0] is True

    def test_german_b1_required(self):
        assert requires_german("Requirements: German B1 level minimum")[0] is True

    def test_verhandlungssicher_deutsch(self):
        assert requires_german("Verhandlungssichere Deutschkenntnisse")[0] is True


# ---------------------------------------------------------------------------
# ATS detection tests
# ---------------------------------------------------------------------------

class TestATSDetection:
    def test_greenhouse_url(self):
        platform, job_id, board = detect_ats(
            "https://boards.greenhouse.io/testcompany/jobs/4567890"
        )
        assert platform == "greenhouse"
        assert job_id == "4567890"
        assert board == "testcompany"

    def test_lever_url(self):
        platform, job_id, board = detect_ats(
            "https://jobs.lever.co/somecompany/abcdef-1234-5678"
        )
        assert platform == "lever"
        assert job_id == "abcdef-1234-5678"
        assert board == "somecompany"

    def test_workable_url(self):
        platform, job_id, board = detect_ats(
            "https://apply.workable.com/acme/j/ABC123/"
        )
        assert platform == "workable"
        assert job_id == "ABC123"
        assert board == "acme"

    def test_personio_url_de(self):
        platform, job_id, board = detect_ats(
            "https://flink-tech.jobs.personio.de/job/987654"
        )
        assert platform == "personio"
        assert job_id == "987654"
        assert board == "flink-tech"

    def test_personio_url_com(self):
        platform, job_id, board = detect_ats(
            "https://acme.jobs.personio.com/job/111222"
        )
        assert platform == "personio"
        assert job_id == "111222"
        assert board == "acme"

    def test_unknown_url(self):
        platform, _, _ = detect_ats("https://careers.example.com/jobs/99")
        assert platform == ""

    def test_html_fallback_greenhouse(self):
        platform, _, _ = detect_ats(
            "https://example.com/careers",
            html='<div data-greenhouse-token="abc"></div>',
        )
        assert platform == "greenhouse"

    def test_smartrecruiters_url(self):
        platform, job_id, board = detect_ats(
            "https://jobs.smartrecruiters.com/MyCo/743999123456"
        )
        assert platform == "smartrecruiters"
        assert job_id == "743999123456"
        assert board == "MyCo"


# ---------------------------------------------------------------------------
# Database + dedup tests
# ---------------------------------------------------------------------------

class TestDatabase:
    def test_save_and_exists(self):
        conn = _in_memory_db()
        job = _make_job()
        assert job_exists(conn, job) is False
        save_job(conn, job)
        assert job_exists(conn, job) is True
        conn.close()

    def test_dedup_by_url(self):
        conn = _in_memory_db()
        job1 = _make_job(url="https://example.com/j/1")
        job2 = _make_job(url="https://example.com/j/1")  # same URL
        save_job(conn, job1)
        assert job_exists(conn, job2) is True
        conn.close()

    def test_dedup_by_title_company(self):
        conn = _in_memory_db()
        job = _make_job(title="Finance Intern", company="BigCo",
                        url="https://source-a.com/1")
        save_job(conn, job)
        assert job_exists_by_title_company(conn, "Finance Intern", "BigCo") is True
        assert job_exists_by_title_company(conn, "finance intern", "bigco") is True  # case insensitive
        assert job_exists_by_title_company(conn, "Finance Intern", "OtherCo") is False
        conn.close()

    def test_dedup_across_sources(self):
        """Same job title+company from two different sources should be caught."""
        conn = _in_memory_db()
        job_a = _make_job(title="Working Student Controlling", company="Jägermeister",
                          url="https://arbeitnow.com/j/1", source="arbeitnow")
        save_job(conn, job_a)

        # Same role from a different source/URL
        assert job_exists_by_title_company(
            conn, "Working Student Controlling", "Jägermeister"
        ) is True
        conn.close()

    def test_log_application(self):
        conn = _in_memory_db()
        job = _make_job()
        save_job(conn, job)
        log_application(conn, job.id, "api_greenhouse", "success")
        row = conn.execute("SELECT COUNT(*) FROM applications WHERE job_id = ?", (job.id,)).fetchone()
        assert row[0] == 1
        conn.close()


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_fresh_db_full_budget(self):
        conn = _in_memory_db()
        assert remaining_applications_today(conn, 5) == 5
        conn.close()

    def test_budget_decreases_with_applications(self):
        conn = _in_memory_db()
        job = _make_job()
        save_job(conn, job)
        from datetime import date
        conn.execute(
            "INSERT INTO applications (job_id, method, status, submitted_at) VALUES (?, ?, ?, ?)",
            (job.id, "api", "success", date.today().isoformat()),
        )
        conn.commit()
        assert remaining_applications_today(conn, 5) == 4
        conn.close()


# ---------------------------------------------------------------------------
# Scoring post-processing tests
# ---------------------------------------------------------------------------

class TestScoringPostProcessing:
    def _score_with_mock(self, jobs, mock_response):
        """Run score_jobs with a mocked OpenAI call."""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = mock_response
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice]
        )
        with patch("core.scorer.OpenAI", return_value=mock_client):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
                from core.scorer import score_jobs
                return score_jobs(jobs)

    def test_marketing_only_capped_at_medium(self):
        job = _make_job(
            title="Working Student Marketing",
            description="Social media management and content creation for our brand.",
        )
        scored = self._score_with_mock(
            [job],
            '{"score": "HIGH", "fit_score": 8, "reason": "Good role type match"}',
        )
        # Marketing-only should be capped to MEDIUM
        assert scored[0].score == "MEDIUM"
        assert "Marketing-only" in scored[0].score_reason

    def test_finance_not_capped(self):
        job = _make_job(
            title="Working Student Finance",
            description="FP&A support, budget analysis, financial reporting.",
        )
        scored = self._score_with_mock(
            [job],
            '{"score": "HIGH", "fit_score": 9, "reason": "Perfect match"}',
        )
        assert scored[0].score == "HIGH"

    def test_marketing_plus_finance_not_capped(self):
        job = _make_job(
            title="Working Student Marketing & Finance",
            description="Marketing analytics with finance KPI tracking and sustainability reporting.",
        )
        scored = self._score_with_mock(
            [job],
            '{"score": "HIGH", "fit_score": 8, "reason": "Good cross-functional match"}',
        )
        # Has both marketing AND core field keywords → not capped
        assert scored[0].score == "HIGH"


# ---------------------------------------------------------------------------
# Email extraction tests
# ---------------------------------------------------------------------------

class TestEmailExtraction:
    def test_apply_email(self):
        text = "Please send your application to hr@company.com"
        assert extract_apply_email(text) == "hr@company.com"

    def test_recruiting_email(self):
        text = "For questions contact recruiting@acme.de"
        assert extract_apply_email(text) == "recruiting@acme.de"

    def test_no_email(self):
        text = "Apply via our online portal."
        assert extract_apply_email(text) == ""


# ---------------------------------------------------------------------------
# Cover letter formatting tests
# ---------------------------------------------------------------------------

class TestCoverLetter:
    def test_format_full_letter(self):
        job = _make_job(title="Working Student FP&A", company="EcoBank")
        body = "I am excited to apply for this role."
        full = _format_full_letter(body, job)
        assert "Amane Aguiar Dias de Azevedo" in full
        assert "Working Student FP&A" in full
        assert "EcoBank" in full
        assert "Dear Hiring Team" in full
        assert body in full


# ---------------------------------------------------------------------------
# Job model tests
# ---------------------------------------------------------------------------

class TestJobModel:
    def test_id_is_url_hash(self):
        job = _make_job(url="https://example.com/j/1")
        assert len(job.id) == 16
        # Same URL → same ID
        job2 = _make_job(url="https://example.com/j/1")
        assert job.id == job2.id

    def test_different_url_different_id(self):
        job1 = _make_job(url="https://example.com/j/1")
        job2 = _make_job(url="https://example.com/j/2")
        assert job1.id != job2.id


# ---------------------------------------------------------------------------
# Integration: in-memory dedup set (mirrors main.py logic)
# ---------------------------------------------------------------------------

class TestInMemoryDedup:
    def test_dedup_within_run(self):
        """The in-memory seen_title_company set prevents same-run duplicates."""
        jobs = [
            _make_job(title="WS Finance", company="Alpha", url="https://a.com/1", source="arbeitnow"),
            _make_job(title="WS Finance", company="Alpha", url="https://b.com/1", source="adzuna"),
            _make_job(title="WS Controlling", company="Beta", url="https://c.com/1", source="arbeitnow"),
        ]

        conn = _in_memory_db()
        seen: set[tuple[str, str]] = set()
        new_jobs = []
        for job in jobs:
            key = (job.title.strip().lower(), job.company.strip().lower())
            if job_exists(conn, job):
                continue
            if job_exists_by_title_company(conn, job.title, job.company):
                continue
            if key in seen:
                continue
            seen.add(key)
            new_jobs.append(job)

        # Alpha duplicate should be dropped
        assert len(new_jobs) == 2
        companies = {j.company for j in new_jobs}
        assert companies == {"Alpha", "Beta"}
        conn.close()


# ---------------------------------------------------------------------------
# Integration: full pipeline flow with all mocks
# ---------------------------------------------------------------------------

class TestFullPipelineFlow:
    """Simulates the main.py run() flow end-to-end with mock data."""

    def test_full_flow(self):
        from unittest.mock import patch
        from langdetect import detect

        # Mock jobs (as if collected)
        mock_jobs = [
            _make_job(title="Working Student Sustainability", company="GreenCo",
                      url="https://boards.greenhouse.io/greenco/jobs/111",
                      description="Support our ESG reporting team. English-speaking environment."),
            _make_job(title="Werkstudent Finanzbuchhalter", company="GermanOnlyCo",
                      url="https://example.de/j/222",
                      description="Wir suchen einen Werkstudenten. Sehr gute Deutschkenntnisse erforderlich."),
            _make_job(title="Working Student Marketing", company="AdCo",
                      url="https://example.com/j/333",
                      description="Social media content creation. English team."),
        ]

        conn = _in_memory_db()

        # Step 1: Dedup (all new)
        seen: set[tuple[str, str]] = set()
        new_jobs = []
        for job in mock_jobs:
            key = (job.title.strip().lower(), job.company.strip().lower())
            if key not in seen:
                seen.add(key)
                new_jobs.append(job)
        assert len(new_jobs) == 3

        # Step 2: German language filter on description
        english_jobs = []
        for job in new_jobs:
            is_german, reason = requires_german(f"{job.title} {job.description}")
            if is_german:
                job.score = "LOW"
                job.score_reason = reason
                save_job(conn, job)
            else:
                english_jobs.append(job)

        # The Finanzbuchhalter one should be filtered (has Deutschkenntnisse)
        assert len(english_jobs) == 2

        # Step 3: ATS detection
        for job in english_jobs:
            platform, job_id, board = detect_ats(job.url)
            job.ats_platform = platform
            job.ats_job_id = job_id
            job.ats_board_token = board

        assert english_jobs[0].ats_platform == "greenhouse"
        assert english_jobs[1].ats_platform == ""

        # Step 4: Mock scoring
        mock_client = MagicMock()
        responses = [
            '{"score": "HIGH", "fit_score": 9, "reason": "Perfect sustainability match"}',
            '{"score": "HIGH", "fit_score": 8, "reason": "Good marketing match"}',
        ]
        mock_choices = []
        for resp in responses:
            choice = MagicMock()
            choice.message.content = resp
            mock_choices.append(MagicMock(choices=[choice]))

        mock_client.chat.completions.create.side_effect = mock_choices
        with patch("core.scorer.OpenAI", return_value=mock_client):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
                from core.scorer import score_jobs
                scored = score_jobs(english_jobs)

        # Sustainability job stays HIGH; marketing-only gets capped to MEDIUM
        sustainability_job = next(j for j in scored if j.company == "GreenCo")
        marketing_job = next(j for j in scored if j.company == "AdCo")

        assert sustainability_job.score == "HIGH"
        assert marketing_job.score == "MEDIUM"
        assert "Marketing-only" in marketing_job.score_reason

        # Step 5: Save all
        for job in scored:
            save_job(conn, job)

        # Verify DB state
        row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        assert row[0] == 3  # 2 English + 1 German-rejected

        conn.close()


# ---------------------------------------------------------------------------
# Apply dispatcher tests
# ---------------------------------------------------------------------------

class TestApplyDispatcher:
    """Tests for delivery/apply_dispatcher.py logic."""

    def _make_profile(self) -> dict:
        return {
            "first_name": "Amane",
            "last_name": "Dias",
            "email": "amane@test.com",
            "resume_path": "",
            "sender_email": "jobs@test.com",
            "auto_apply": {
                "enabled": True,
                "tiers": ["MEDIUM"],
                "max_per_day": 5,
                "max_retries": 3,
                "methods": {"api": False, "browser": False, "email": False},
            },
            "candidate": {
                "screening_answers": {},
            },
        }

    def test_per_company_dedup_skips_second_application(self):
        """Second application to same company within 7 days is skipped."""
        from delivery.apply_dispatcher import apply_to_jobs

        conn = _in_memory_db()
        profile = self._make_profile()

        # First job at CompanyA — already applied (in DB)
        job_a1 = _make_job(title="WS Finance", company="CompanyA",
                           url="https://a.com/1", source="adzuna")
        job_a1.score = "MEDIUM"
        save_job(conn, job_a1)
        log_application(conn, job_a1.id, "api_greenhouse", "success")

        # Second job at CompanyA — should be skipped
        job_a2 = _make_job(title="WS Controlling", company="CompanyA",
                           url="https://a.com/2", source="adzuna")
        job_a2.score = "MEDIUM"

        result = apply_to_jobs([job_a2], profile, conn, dry_run=True)
        assert result[0].status == "apply_skipped_company_dup"
        conn.close()

    def test_rate_limit_stops_applying(self):
        """After budget is exhausted, remaining jobs are queued."""
        from delivery.apply_dispatcher import apply_to_jobs
        from datetime import date

        conn = _in_memory_db()
        profile = self._make_profile()
        profile["auto_apply"]["max_per_day"] = 1

        # Pre-fill 1 application today to exhaust budget
        existing = _make_job(title="Already Applied", company="OldCo",
                             url="https://old.com/1")
        existing.score = "MEDIUM"
        existing.status = "auto_applied"
        save_job(conn, existing)
        conn.execute(
            "INSERT INTO applications (job_id, method, status, submitted_at) VALUES (?, ?, ?, ?)",
            (existing.id, "api", "success", date.today().isoformat()),
        )
        conn.commit()

        # New job should be queued, not applied
        new_job = _make_job(title="WS Finance", company="NewCo",
                            url="https://new.com/1")
        new_job.score = "MEDIUM"

        result = apply_to_jobs([new_job], profile, conn, dry_run=True)
        assert result[0].status == "queued"
        conn.close()

    def test_dry_run_returns_success_without_submitting(self):
        """In dry_run mode with no methods enabled, job gets marked quick_apply."""
        from delivery.apply_dispatcher import apply_to_jobs

        conn = _in_memory_db()
        profile = self._make_profile()

        job = _make_job(title="WS Finance", company="TestCo",
                        url="https://test.com/1")
        job.score = "MEDIUM"

        with patch("delivery.apply_dispatcher.generate_cover_letter", return_value="Test letter"):
            result = apply_to_jobs([job], profile, conn, dry_run=True)

        # No methods enabled → quick_apply
        assert result[0].status == "quick_apply"
        conn.close()

    def test_no_apply_method_marks_quick_apply(self):
        """When no apply method is available, job is marked quick_apply."""
        from delivery.apply_dispatcher import apply_to_jobs

        conn = _in_memory_db()
        profile = self._make_profile()

        job = _make_job(title="WS Sustainability", company="GreenCo",
                        url="https://green.com/1")
        job.score = "MEDIUM"
        job.cover_letter = "Pre-existing cover letter"

        result = apply_to_jobs([job], profile, conn, dry_run=True)
        assert result[0].status == "quick_apply"
        conn.close()


# ---------------------------------------------------------------------------
# Collector tests
# ---------------------------------------------------------------------------

class TestAdzunaCollector:
    """Tests for collectors/adzuna.py."""

    SAMPLE_RESPONSE = {
        "results": [
            {
                "title": "Working Student Finance",
                "company": {"display_name": "TestBank AG"},
                "location": {"display_name": "Berlin"},
                "description": "<b>FP&amp;A support</b> needed. English team.",
                "redirect_url": "https://adzuna.de/j/1",
            },
            {
                "title": "Intern Sustainability",
                "company": {"display_name": "GreenCo"},
                "location": {"display_name": "Munich"},
                "description": "Join our ESG team.",
                "redirect_url": "https://adzuna.de/j/2",
            },
        ]
    }

    def test_parses_valid_response(self):
        from collectors.adzuna import AdzunaCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = self.SAMPLE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.adzuna.requests.Session", return_value=mock_session):
            with patch.dict("os.environ", {"ADZUNA_APP_ID": "test", "ADZUNA_APP_KEY": "key"}):
                collector = AdzunaCollector()
                jobs = collector.collect()

        assert len(jobs) >= 2
        assert jobs[0].title == "Working Student Finance"
        assert jobs[0].company == "TestBank AG"
        assert jobs[0].source == "adzuna"
        # HTML tags should be stripped from description
        assert "<b>" not in jobs[0].description

    def test_handles_missing_fields(self):
        """Minimal response with missing fields should not crash."""
        from collectors.adzuna import AdzunaCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"redirect_url": "https://adzuna.de/j/minimal"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.adzuna.requests.Session", return_value=mock_session):
            with patch.dict("os.environ", {"ADZUNA_APP_ID": "test", "ADZUNA_APP_KEY": "key"}):
                collector = AdzunaCollector()
                jobs = collector.collect()

        # Should parse without crashing, using defaults
        assert any(j.title == "Unknown" for j in jobs)

    def test_skips_without_api_keys(self):
        from collectors.adzuna import AdzunaCollector

        with patch.dict("os.environ", {}, clear=True):
            collector = AdzunaCollector()
            jobs = collector.collect()
        assert jobs == []

    def test_deduplicates_by_url(self):
        from collectors.adzuna import AdzunaCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"title": "Job A", "redirect_url": "https://adzuna.de/j/same"},
                {"title": "Job B", "redirect_url": "https://adzuna.de/j/same"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.adzuna.requests.Session", return_value=mock_session):
            with patch.dict("os.environ", {"ADZUNA_APP_ID": "test", "ADZUNA_APP_KEY": "key"}):
                collector = AdzunaCollector()
                jobs = collector.collect()

        urls = [j.url for j in jobs]
        assert urls.count("https://adzuna.de/j/same") == 1


class TestArbeitnowCollector:
    """Tests for collectors/arbeitnow.py."""

    SAMPLE_RESPONSE = {
        "data": [
            {
                "title": "Working Student Finance",
                "company_name": "FinCo",
                "location": "Berlin",
                "description": "Join our FP&A team as a working student.",
                "url": "https://arbeitnow.com/j/1",
                "tags": ["finance", "working student"],
            },
        ],
        "links": {"next": None},
    }

    def test_parses_valid_response(self):
        from collectors.arbeitnow import ArbeitnowCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = self.SAMPLE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.arbeitnow.requests.Session", return_value=mock_session):
            collector = ArbeitnowCollector()
            jobs = collector.collect()

        assert len(jobs) >= 1
        assert jobs[0].title == "Working Student Finance"
        assert jobs[0].company == "FinCo"
        assert jobs[0].source == "arbeitnow"

    def test_filters_irrelevant_jobs(self):
        """Jobs that don't match role + field keywords should be filtered out."""
        from collectors.arbeitnow import ArbeitnowCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "title": "Senior Software Engineer",
                    "company_name": "TechCo",
                    "location": "Berlin",
                    "description": "Build microservices at scale.",
                    "url": "https://arbeitnow.com/j/irrelevant",
                    "tags": ["engineering"],
                },
            ],
            "links": {"next": None},
        }
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.arbeitnow.requests.Session", return_value=mock_session):
            collector = ArbeitnowCollector()
            jobs = collector.collect()

        assert len(jobs) == 0

    def test_handles_empty_response(self):
        from collectors.arbeitnow import ArbeitnowCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [], "links": {}}
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.arbeitnow.requests.Session", return_value=mock_session):
            collector = ArbeitnowCollector()
            jobs = collector.collect()

        assert jobs == []


# ---------------------------------------------------------------------------
# Email digest tests
# ---------------------------------------------------------------------------

class TestEmailDigest:
    """Tests for delivery/email.py build_digest and send_digest."""

    def test_html_escapes_special_characters(self):
        """Company names with & and < should be rendered safely."""
        job = _make_job(company="Ernst & Young", title="Intern <Finance>")
        job.score = "HIGH"
        job.score_reason = "FP&A match"
        _, body = build_digest([job])
        assert "&amp;" in body
        assert "&lt;" in body

    def test_categorizes_auto_applied_jobs(self):
        """Auto-applied jobs should appear in the AUTO-APPLIED section."""
        job = _make_job()
        job.score = "MEDIUM"
        job.status = "auto_applied"
        job.apply_method = "api_greenhouse"
        _, body = build_digest([job])
        assert "AUTO-APPLIED" in body

    def test_empty_jobs_returns_early(self):
        """send_digest with empty list should not crash."""
        # Should return without error
        send_digest([], "test@test.com")


# ---------------------------------------------------------------------------
# Himalayas and Indeed RSS collector tests
# ---------------------------------------------------------------------------

class TestHimalayasCollector:
    """Tests for collectors/himalayas.py."""

    SAMPLE_RESPONSE = {
        "jobs": [
            {
                "title": "Working Student Sustainability",
                "companyName": "GreenTech GmbH",
                "applicationLink": "https://himalayas.app/j/1",
                "excerpt": "<p>Join our sustainability team.</p>",
            },
            {
                "title": "Senior Software Engineer",
                "companyName": "TechCo",
                "applicationLink": "https://himalayas.app/j/2",
                "excerpt": "Build backend services.",
            },
        ]
    }

    def test_parses_valid_response(self):
        from collectors.himalayas import HimalayasCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = self.SAMPLE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.himalayas.requests.Session", return_value=mock_session):
            collector = HimalayasCollector()
            jobs = collector.collect()

        # Only the "Working Student" job should pass the role keyword filter
        matching = [j for j in jobs if j.company == "GreenTech GmbH"]
        assert len(matching) >= 1
        assert matching[0].source == "himalayas"
        # HTML should be stripped from description
        assert "<p>" not in matching[0].description

    def test_filters_non_role_keywords(self):
        """Jobs without intern/working student in title are excluded."""
        from collectors.himalayas import HimalayasCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {
                    "title": "Senior Finance Manager",
                    "companyName": "BigCo",
                    "applicationLink": "https://himalayas.app/j/senior",
                    "excerpt": "Lead the finance team.",
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.himalayas.requests.Session", return_value=mock_session):
            collector = HimalayasCollector()
            jobs = collector.collect()

        assert len(jobs) == 0

    def test_handles_empty_response(self):
        from collectors.himalayas import HimalayasCollector

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": []}
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("collectors.himalayas.requests.Session", return_value=mock_session):
            collector = HimalayasCollector()
            jobs = collector.collect()

        assert jobs == []


# ---------------------------------------------------------------------------
# save_job ON CONFLICT behavior tests
# ---------------------------------------------------------------------------

class TestSaveJobConflict:
    """Tests for save_job's ON CONFLICT(id) DO UPDATE logic."""

    def test_resave_preserves_cover_letter(self):
        """Re-saving a job with empty cover_letter should keep the original."""
        conn = _in_memory_db()
        job = _make_job()
        job.cover_letter = "Original letter"
        save_job(conn, job)

        # Re-save same job with empty cover letter
        job2 = _make_job(url=job.url)  # same URL = same ID
        job2.cover_letter = ""
        save_job(conn, job2)

        row = conn.execute("SELECT cover_letter FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row[0] == "Original letter"
        conn.close()

    def test_resave_updates_score(self):
        """Re-saving a job with a new score should update it."""
        conn = _in_memory_db()
        job = _make_job()
        job.score = "LOW"
        save_job(conn, job)

        job.score = "HIGH"
        save_job(conn, job)

        row = conn.execute("SELECT score FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row[0] == "HIGH"
        conn.close()

    def test_resave_preserves_status(self):
        """Re-saving with status='new' should not overwrite a meaningful status."""
        conn = _in_memory_db()
        job = _make_job()
        job.status = "auto_applied"
        save_job(conn, job)

        job2 = _make_job(url=job.url)
        job2.status = "new"
        save_job(conn, job2)

        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row[0] == "auto_applied"
        conn.close()

    def test_resave_keeps_max_apply_attempts(self):
        """Re-saving should keep the higher apply_attempts count."""
        conn = _in_memory_db()
        job = _make_job()
        job.apply_attempts = 3
        save_job(conn, job)

        job2 = _make_job(url=job.url)
        job2.apply_attempts = 1
        save_job(conn, job2)

        row = conn.execute("SELECT apply_attempts FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row[0] == 3
        conn.close()


class TestIndeedRSSCollector:
    """Tests for collectors/indeed_rss.py."""

    def _mock_feed(self, entries):
        feed = MagicMock()
        feed.entries = entries
        return feed

    def test_parses_valid_feed(self):
        from collectors.indeed_rss import IndeedRSSCollector

        entry = MagicMock()
        entry.get = lambda k, d="": {
            "link": "https://indeed.com/j/1",
            "title": "Working Student Finance",
            "summary": "<b>Great opportunity</b> in finance.",
        }.get(k, d)
        entry.source = MagicMock()
        entry.source.title = "FinCo AG"

        with patch("collectors.indeed_rss.feedparser.parse", return_value=self._mock_feed([entry])):
            collector = IndeedRSSCollector(["https://indeed.com/rss/1"])
            jobs = collector.collect()

        assert len(jobs) == 1
        assert jobs[0].title == "Working Student Finance"
        assert jobs[0].company == "FinCo AG"
        assert jobs[0].source == "indeed"
        # HTML should be stripped
        assert "<b>" not in jobs[0].description

    def test_deduplicates_by_url(self):
        from collectors.indeed_rss import IndeedRSSCollector

        entry = MagicMock()
        entry.get = lambda k, d="": {
            "link": "https://indeed.com/j/same",
            "title": "Job A",
            "summary": "Desc",
        }.get(k, d)
        entry.source = MagicMock()
        entry.source.title = "Co"

        # Same entry in two feeds
        with patch("collectors.indeed_rss.feedparser.parse", return_value=self._mock_feed([entry, entry])):
            collector = IndeedRSSCollector(["https://indeed.com/rss/1"])
            jobs = collector.collect()

        urls = [j.url for j in jobs]
        assert urls.count("https://indeed.com/j/same") == 1

    def test_handles_empty_feed(self):
        from collectors.indeed_rss import IndeedRSSCollector

        with patch("collectors.indeed_rss.feedparser.parse", return_value=self._mock_feed([])):
            collector = IndeedRSSCollector(["https://indeed.com/rss/1"])
            jobs = collector.collect()

        assert jobs == []


# ---------------------------------------------------------------------------
# Enricher tests
# ---------------------------------------------------------------------------

class TestEnricher:
    """Tests for core/enricher.py enrich_jobs() with mocked HTTP."""

    def _mock_urlopen(self, html_content, final_url=None, status=200):
        """Create a mock for urllib.request.urlopen."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_content.encode("utf-8")
        mock_resp.url = final_url or "https://example.com/job/1"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_german_required_job_rejected(self):
        """Job with German requirement in description is filtered out."""
        from core.enricher import enrich_jobs

        job = _make_job(
            title="Working Student Finance",
            url="https://example.com/job/german",
            description="Short desc",
        )

        html_content = "<html><body>Join our team. Sehr gute Deutschkenntnisse erforderlich. Financial analysis role.</body></html>"
        mock_resp = self._mock_urlopen(html_content, final_url="https://example.com/job/german")

        with patch("core.enricher.urllib.request.urlopen", return_value=mock_resp):
            english, german = enrich_jobs([job])

        assert len(german) == 1
        assert len(english) == 0
        assert german[0].score == "LOW"
        assert "German required" in german[0].score_reason

    def test_apply_email_extracted(self):
        """Apply email is extracted from job description."""
        from core.enricher import enrich_jobs

        job = _make_job(
            title="Working Student Finance",
            url="https://example.com/job/email",
            description="Short desc",
        )

        html_content = "<html><body>Working student finance role. Please send your application to hr@greencorp.com for consideration.</body></html>"
        mock_resp = self._mock_urlopen(html_content, final_url="https://example.com/job/email")

        with patch("core.enricher.urllib.request.urlopen", return_value=mock_resp):
            english, german = enrich_jobs([job])

        assert len(english) == 1
        assert english[0].apply_email == "hr@greencorp.com"

    def test_ats_platform_detected(self):
        """ATS platform is detected from enriched URL."""
        from core.enricher import enrich_jobs

        job = _make_job(
            title="Working Student FP&A",
            url="https://boards.greenhouse.io/testco/jobs/123456",
            description="Short desc",
        )

        html_content = "<html><body>FP&A working student needed. English team.</body></html>"
        mock_resp = self._mock_urlopen(html_content, final_url="https://boards.greenhouse.io/testco/jobs/123456")

        with patch("core.enricher.urllib.request.urlopen", return_value=mock_resp):
            english, german = enrich_jobs([job])

        assert len(english) == 1
        assert english[0].ats_platform == "greenhouse"
        assert english[0].ats_job_id == "123456"

    def test_url_updated_after_redirect(self):
        """Job URL is updated to the final URL after redirect."""
        from core.enricher import enrich_jobs

        job = _make_job(
            title="Working Student Finance",
            url="https://redirect.example.com/j/short",
            description="Short desc",
        )

        html_content = "<html><body>Finance working student position. English only.</body></html>"
        mock_resp = self._mock_urlopen(html_content, final_url="https://careers.example.com/jobs/12345")

        with patch("core.enricher.urllib.request.urlopen", return_value=mock_resp):
            english, german = enrich_jobs([job])

        assert len(english) == 1
        assert english[0].url == "https://careers.example.com/jobs/12345"

    def test_retry_on_http_500(self):
        """Enricher retries on HTTP 500 errors."""
        from core.enricher import fetch_full_description
        import urllib.error

        call_count = 0

        def side_effect(req, timeout=10):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise urllib.error.HTTPError(
                    url="https://example.com/job/1", code=500,
                    msg="Server Error", hdrs={}, fp=None,
                )
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"<html><body>Success content</body></html>"
            mock_resp.url = "https://example.com/job/1"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("core.enricher.urllib.request.urlopen", side_effect=side_effect):
            with patch("core.enricher.time.sleep"):  # Skip actual sleep
                text, url, raw = fetch_full_description("https://example.com/job/1")

        assert call_count == 3
        assert text is not None
        assert "Success content" in text
