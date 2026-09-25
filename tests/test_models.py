from datetime import datetime, timedelta, timezone

from conftest import make_repo

from gitkeeper.models import parse_datetime
from gitkeeper.providers.base import normalize_topics, pick_readme
from gitkeeper.render import one_line, relative_time


def test_parse_datetime_variants():
    assert parse_datetime("2025-01-02T03:04:05Z") == datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert parse_datetime("2025-01-02T03:04:05.123+02:00").utcoffset() == timedelta(hours=2)
    assert parse_datetime("2025-01-02T03:04:05").tzinfo == timezone.utc
    assert parse_datetime(None) is None
    assert parse_datetime("garbage") is None


def test_repo_matches_all_words():
    repo = make_repo("gitkeeper", description="CLI para repos", language="Python", topics=["ai-tools"])
    assert repo.matches("gitkeeper")
    assert repo.matches("python cli")
    assert repo.matches("AI-TOOLS")
    assert not repo.matches("python rust")


def test_repo_to_dict():
    repo = make_repo(visibility="private", archived=True)
    data = repo.to_dict()
    assert data["visibility"] == "private"
    assert data["archived"] is True
    assert data["created_at"].startswith("2026-08-02")
    assert "raw" not in data


def test_normalize_topics():
    assert normalize_topics([" Web Dev ", "web_dev", "C#", "", "--x--", "a" * 60]) == ["web-dev", "c", "x", "a" * 50]
    assert len(normalize_topics([f"t{i}" for i in range(30)])) == 20


def test_pick_readme_preference():
    assert pick_readme(["setup.py", "README.txt", "README.md", "readme"]) == "README.md"
    assert pick_readme(["Readme.rst", "README"]) == "Readme.rst"
    assert pick_readme(["README"]) == "README"
    assert pick_readme(["main.py", "READMEFIRST.md"]) is None


def test_relative_time():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert relative_time(None) == "—"
    assert relative_time(now - timedelta(seconds=5), now) == "hace un momento"
    assert relative_time(now - timedelta(minutes=1), now) == "hace 1 minuto"
    assert relative_time(now - timedelta(hours=3), now) == "hace 3 horas"
    assert relative_time(now - timedelta(days=1), now) == "hace 1 día"
    assert relative_time(now - timedelta(days=15), now) == "hace 2 semanas"
    assert relative_time(now - timedelta(days=400), now) == "hace 1 año"
    assert relative_time(now + timedelta(days=1), now) == "ahora"


def test_one_line():
    assert one_line("GitLab FOSS\n\nis  a mirror\n") == "GitLab FOSS is a mirror"
