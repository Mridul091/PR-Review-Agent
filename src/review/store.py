import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import aiosqlite

from src.review.schemas import ReviewResult


class ReviewStore(Protocol):
    """Persistence contract for review results."""

    async def initialize(self):
        """Prepare the backing store for reads and writes."""

    async def save_result(self, result: ReviewResult):
        """Persist or replace a review result."""

    async def get_result(self, review_id: str) -> ReviewResult | None:
        """Return a review result by ID, if present."""

    async def claim_review(
        self,
        delivery_id: str,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        head_sha: str,
    ) -> bool:
        """Atomically record accepted work; return False if it was already claimed."""
        ...


class InMemoryReviewStore:
    """Store review results in memory for tests and app instances without lifespan startup."""

    def __init__(self):
        self._results: dict[str, ReviewResult] = {}
        self._claimed_deliveries: set[str] = set()
        self._claimed_identities: set[tuple[int, str, int, str]] = set()

    async def initialize(self):
        return None

    async def save_result(self, result: ReviewResult):
        self._results[result.review_id] = result

    async def get_result(self, review_id: str):
        return self._results.get(review_id)

    async def claim_review(
        self,
        delivery_id: str,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        head_sha: str,
    ) -> bool:
        identity = (installation_id, repository, pull_request_number, head_sha)
        # No await between the check and the insert, so this cannot interleave.
        if delivery_id in self._claimed_deliveries or identity in self._claimed_identities:
            return False
        self._claimed_deliveries.add(delivery_id)
        self._claimed_identities.add(identity)
        return True


class SQLiteReviewStore:
    """Persist serialized review results in a SQLite database."""

    _schema_version = 1
    _current_columns = {
        "review_id",
        "request_json",
        "result_json",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "usage_json",
        "failures_json",
        "created_at",
    }
    _legacy_columns = {"review_id", "result_json", "created_at"}
    _required_columns = {
        "request_json",
        "result_json",
        "status",
        "started_at",
        "duration_ms",
        "usage_json",
        "failures_json",
        "created_at",
    }

    def __init__(self, database_url: str):
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must use the sqlite:/// scheme")
        self.database_path = database_url.removeprefix(prefix)
        if self.database_path == ":memory:":
            raise ValueError(
                "SQLiteReviewStore requires a file-backed database; :memory: is unsupported"
            )
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                version = await self._get_schema_version(db)
                column_info = await self._get_review_column_info(db)
                columns = set(column_info)
                schema_is_canonical = self._has_canonical_constraints(column_info)

                if version > self._schema_version:
                    raise RuntimeError(
                        f"Database schema version {version} is newer than supported "
                        f"version {self._schema_version}"
                    )

                if version == self._schema_version:
                    if columns != self._current_columns:
                        raise RuntimeError("Review table does not match schema version 1")
                    if not schema_is_canonical:
                        if await self._review_count(db):
                            raise RuntimeError(
                                "Version 1 review table has incompatible constraints "
                                "and contains rows"
                            )
                        await db.execute("DROP TABLE reviews")
                        await self._create_reviews_table(db)
                elif not columns:
                    await self._create_reviews_table(db)
                    await self._set_schema_version(db, self._schema_version)
                elif columns == self._current_columns:
                    if await self._review_count(db):
                        raise RuntimeError(
                            "Cannot adopt unversioned review table because it contains rows"
                        )
                    if not schema_is_canonical:
                        await db.execute("DROP TABLE reviews")
                        await self._create_reviews_table(db)
                    await self._set_schema_version(db, self._schema_version)
                elif columns == self._legacy_columns:
                    if await self._review_count(db):
                        raise RuntimeError(
                            "Cannot migrate legacy review table because it contains rows"
                        )
                    await db.execute("DROP TABLE reviews")
                    await self._create_reviews_table(db)
                    await self._set_schema_version(db, self._schema_version)
                else:
                    raise RuntimeError(f"Unexpected reviews table columns: {sorted(columns)}")

                # Outside the user_version check: IF NOT EXISTS keeps this safe on
                # every startup and leaves the reviews migration logic untouched.
                await self._create_review_claims_table(db)

                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _get_schema_version(self, db: aiosqlite.Connection) -> int:
        async with db.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def _set_schema_version(self, db: aiosqlite.Connection, version: int):
        await db.execute(f"PRAGMA user_version = {version}")

    async def _get_review_column_info(self, db: aiosqlite.Connection):
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reviews'"
        ) as cursor:
            if await cursor.fetchone() is None:
                return {}
        async with db.execute("PRAGMA table_info(reviews)") as cursor:
            return {row[1]: (row[2].upper(), row[3], row[5]) for row in await cursor.fetchall()}

    def _has_canonical_constraints(self, column_info: dict) -> bool:
        if set(column_info) != self._current_columns:
            return False
        for name in self._required_columns:
            if column_info[name][1] != 1:
                return False
        if column_info["review_id"][2] != 1:
            return False
        if column_info["finished_at"][1] != 0:
            return False
        return True

    async def _review_count(self, db: aiosqlite.Connection) -> int:
        async with db.execute("SELECT COUNT(*) FROM reviews") as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def _create_reviews_table(self, db: aiosqlite.Connection):
        await db.execute("""
            CREATE TABLE reviews (
                review_id TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER NOT NULL,
                usage_json TEXT NOT NULL,
                failures_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """)

    async def _create_review_claims_table(self, db: aiosqlite.Connection):
        # A claim records that a webhook delivery was accepted, before any result
        # exists. Two UNIQUE rules: the delivery (redelivery of the same event) and
        # the review identity (different deliveries asking for the same work).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS review_claims (
                delivery_id TEXT PRIMARY KEY,
                installation_id INTEGER NOT NULL,
                repository TEXT NOT NULL,
                pull_request_number INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                review_id TEXT,
                status TEXT NOT NULL
                    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
                created_at TEXT NOT NULL,
                UNIQUE (installation_id, repository, pull_request_number, head_sha)
            )
            """)

    async def save_result(self, result: ReviewResult):
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO reviews (
                    review_id,
                    request_json,
                    result_json,
                    status,
                    started_at,
                    finished_at,
                    duration_ms,
                    usage_json,
                    failures_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    request_json = excluded.request_json,
                    result_json = excluded.result_json,
                    status = excluded.status,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    duration_ms = excluded.duration_ms,
                    usage_json = excluded.usage_json,
                    failures_json = excluded.failures_json,
                    created_at = excluded.created_at
                """,
                (
                    result.review_id,
                    result.request.model_dump_json(),
                    result.model_dump_json(),
                    result.status.value,
                    result.started_at.isoformat(),
                    result.finished_at.isoformat() if result.finished_at else None,
                    result.duration_ms,
                    result.usage.model_dump_json(),
                    json.dumps(
                        [failure.model_dump(mode="json") for failure in result.failures],
                        separators=(",", ":"),
                    ),
                    result.started_at.isoformat(),
                ),
            )
            await db.commit()

    async def get_result(self, review_id: str) -> ReviewResult | None:
        async with aiosqlite.connect(self.database_path) as db:
            async with db.execute(
                "SELECT result_json FROM reviews WHERE review_id = ?",
                (review_id,),
            ) as cursor:
                row = await cursor.fetchone()

        return ReviewResult.model_validate_json(row[0]) if row else None

    async def claim_review(
        self,
        delivery_id: str,
        installation_id: int,
        repository: str,
        pull_request_number: int,
        head_sha: str,
    ) -> bool:
        # The UNIQUE constraints decide the winner, so concurrent claims cannot both succeed.
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO review_claims (
                    delivery_id,
                    installation_id,
                    repository,
                    pull_request_number,
                    head_sha,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    delivery_id,
                    installation_id,
                    repository,
                    pull_request_number,
                    head_sha,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
            return cursor.rowcount == 1
