import aiosqlite
import pytest

from src.review.schemas import ReviewFailure, ReviewRequest, ReviewResult, ReviewStatus
from src.review.store import SQLiteReviewStore


def test_sqlite_store_rejects_in_memory_database():
    with pytest.raises(ValueError, match="file-backed database"):
        SQLiteReviewStore("sqlite:///:memory:")


async def _schema_version(database_path):
    async with aiosqlite.connect(database_path) as db:
        async with db.execute("PRAGMA user_version") as cursor:
            return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_current_empty_schema_is_versioned_idempotently(tmp_path):
    database_path = tmp_path / "reviews.db"
    store = SQLiteReviewStore(f"sqlite:///{database_path}")

    await store.initialize()
    assert await _schema_version(database_path) == 1
    await store.initialize()
    assert await _schema_version(database_path) == 1


@pytest.mark.asyncio
async def test_empty_legacy_table_is_rebuilt_to_current_schema(tmp_path):
    database_path = tmp_path / "reviews.db"
    async with aiosqlite.connect(database_path) as db:
        await db.execute(
            "CREATE TABLE reviews (review_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        await db.commit()

    store = SQLiteReviewStore(f"sqlite:///{database_path}")
    await store.initialize()

    async with aiosqlite.connect(database_path) as db:
        async with db.execute("PRAGMA table_info(reviews)") as cursor:
            column_info = {row[1]: row for row in await cursor.fetchall()}
    assert set(column_info) == store._current_columns
    assert all(column_info[name][3] == 1 for name in store._required_columns)
    assert column_info["review_id"][5] == 1
    assert column_info["finished_at"][3] == 0
    assert await _schema_version(database_path) == 1


@pytest.mark.asyncio
async def test_empty_versioned_table_with_weak_constraints_is_rebuilt(tmp_path):
    database_path = tmp_path / "reviews.db"
    async with aiosqlite.connect(database_path) as db:
        await db.execute("""
            CREATE TABLE reviews (
                review_id TEXT PRIMARY KEY,
                request_json TEXT,
                result_json TEXT,
                status TEXT,
                started_at TEXT,
                finished_at TEXT,
                duration_ms INTEGER,
                usage_json TEXT,
                failures_json TEXT,
                created_at TEXT
            )
            """)
        await db.execute("PRAGMA user_version = 1")
        await db.commit()

    await SQLiteReviewStore(f"sqlite:///{database_path}").initialize()

    async with aiosqlite.connect(database_path) as db:
        async with db.execute("PRAGMA table_info(reviews)") as cursor:
            column_info = {row[1]: row for row in await cursor.fetchall()}
    assert all(column_info[name][3] == 1 for name in SQLiteReviewStore._required_columns)
    assert column_info["review_id"][5] == 1
    assert column_info["finished_at"][3] == 0
    assert await _schema_version(database_path) == 1


@pytest.mark.asyncio
async def test_nonempty_legacy_table_is_rejected_without_data_loss(tmp_path):
    database_path = tmp_path / "reviews.db"
    async with aiosqlite.connect(database_path) as db:
        await db.execute(
            "CREATE TABLE reviews (review_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO reviews VALUES (?, ?, ?)",
            ("legacy-id", "{}", "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()

    store = SQLiteReviewStore(f"sqlite:///{database_path}")
    with pytest.raises(RuntimeError, match="contains rows"):
        await store.initialize()

    async with aiosqlite.connect(database_path) as db:
        async with db.execute("SELECT review_id FROM reviews") as cursor:
            assert await cursor.fetchone() == ("legacy-id",)
    assert await _schema_version(database_path) == 0


@pytest.mark.asyncio
async def test_review_result_persists_across_store_instances(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'reviews.db'}"
    first_store = SQLiteReviewStore(database_url)
    await first_store.initialize()

    result = ReviewResult(
        review_id="review-123",
        request=ReviewRequest(
            repository="owner/repo",
            installation_id=123,
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
        ),
        status=ReviewStatus.COMPLETED,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
        duration_ms=1000,
    )
    await first_store.save_result(result)

    second_store = SQLiteReviewStore(database_url)
    persisted = await second_store.get_result(result.review_id)

    assert persisted == result
    assert await second_store.get_result("missing") is None


@pytest.mark.asyncio
async def test_review_result_fields_are_queryable_columns(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'reviews.db'}"
    store = SQLiteReviewStore(database_url)
    await store.initialize()

    result = ReviewResult(
        review_id="review-456",
        request=ReviewRequest(
            repository="owner/repo",
            installation_id=123,
            pull_request_number=8,
            base_sha="a" * 40,
            head_sha="b" * 40,
        ),
        status=ReviewStatus.FAILED,
        failures=[
            ReviewFailure(
                code="provider_error",
                stage="review",
                message="Provider failed.",
                retryable=True,
                agent="bug_detector",
            )
        ],
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:02Z",
        duration_ms=2000,
    )
    await store.save_result(result)

    async with aiosqlite.connect(store.database_path) as db:
        async with db.execute(
            """
            SELECT request_json, status, started_at, finished_at,
                   duration_ms, usage_json, failures_json
            FROM reviews
            WHERE review_id = ?
            """,
            (result.review_id,),
        ) as cursor:
            row = await cursor.fetchone()

    assert row is not None
    assert '"repository":"owner/repo"' in row[0]
    assert row[1] == "failed"
    assert row[2] == "2026-01-01T00:00:00+00:00"
    assert row[3] == "2026-01-01T00:00:02+00:00"
    assert row[4] == 2000
    assert '"model_calls":0' in row[5]
    assert '"code":"provider_error"' in row[6]
