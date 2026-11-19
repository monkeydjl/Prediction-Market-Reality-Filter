"""The health payload must be the same answer however often it is asked.

§53.113 wanted to assert that ``HEAD /api/health`` reports the same
``content-length`` as the GET it stands in for, and could not: two calls on a
cold loop database came back 1 256 and 1 293 bytes. The cause was not a
timestamp (the first guess, and it was wrong) but the order of the reads:

* ``storage.loop_db_schema_versions`` was snapshotted **while the response dict
  was being built**, and ``list_pending()`` / ``latest_run_per_job()`` -- which
  create two of the tables that snapshot reports -- run later in that same
  literal. The first poll therefore described the database as it was *before
  the request created it*: ``{"event_market_links": null, "loop_runs": null}``,
  then ``{...: 1}`` on every poll after. Measured on a fresh sandbox, exactly
  those two keys flipped, null -> 1.
* ``_prediction_counts()`` ran before ``calibration_summary()``, and only the
  latter creates the ``predictions`` table, so the first poll ran a query
  against a missing table, logged a warning carrying a full traceback, and
  reported ``{}``.

Both are the same shape: an answer that is only correct after a warm-up. A
health payload is polled by monitors that never warm up, and a HEAD that
reports a different size from the GET behind it is worse than no HEAD at all
-- it is a number a client would trust.

The queue-age float is *not* part of this: ``oldest_age_hours`` is rounded to
four decimals (0.36 s), so it is excluded by construction here -- these tests
run against an empty review queue, where the field is null.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402

JSON = "application/json"


class ColdLoopDatabaseTests(unittest.TestCase):
    """A database this process has never opened: the worst case for a payload."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Only the loop DB is redirected. The event store stays where the
        # session sandbox put it, so `event_store_bytes` / record counts do not
        # move between the two calls either.
        overrides = patch.object(
            settings, "LOOP_DB_FILE", str(Path(self._tmp.name) / "cold_loop.db")
        )
        overrides.start()
        self.addCleanup(overrides.stop)

        from app.main import app as real_app

        self.client = TestClient(real_app)

    def test_the_first_poll_answers_what_the_second_poll_answers(self):
        first = self.client.get("/api/health")
        second = self.client.get("/api/health")

        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(
            first.content,
            second.content,
            "the payload changed between two identical requests: some part of it "
            "is a function of when it was asked, not of the state it describes",
        )

    def test_the_schema_snapshot_reports_what_the_request_created(self):
        """Empty means it was taken before the readers ran -- the old defect.

        The snapshot has to be non-empty on the *first* poll of a database: the
        request creates the tables, so a payload that reports nothing was read
        before that happened. This is the assertion the "snapshot early"
        behaviour fails, whether it shows up as `{}` or as a null-filled dict.
        """
        body = self.client.get("/api/health").json()

        versions = body["loop"]["storage"]["loop_db_schema_versions"]
        self.assertTrue(
            versions,
            "the schema snapshot reported nothing after a request that created "
            "the schema: it was read before the loop stores were touched",
        )
        self.assertNotIn(None, versions.values(), "a component this call created is still null")

    def test_head_reports_the_length_of_the_get_it_stands_in_for(self):
        """The reason §53.113 exists: `curl -I` has to tell the truth about size."""
        head = self.client.head("/api/health")
        get = self.client.get("/api/health")

        self.assertEqual(head.status_code, get.status_code)
        self.assertEqual(head.headers.get("content-type"), JSON)
        self.assertEqual(
            head.headers.get("content-length"),
            str(len(get.content)),
            "HEAD advertised a different body size than the GET it stands in for",
        )

    def test_the_first_poll_does_not_log_a_failed_query(self):
        """A cold start must not write a traceback into the logs.

        ``_prediction_counts`` swallows its exception (a status endpoint that
        raises is worse) but logged it at WARNING with ``exc_info`` -- so the
        first poll of a fresh database shipped a query traceback to the log
        file. Ordering the calibration read first removes the missing table,
        and these two readings are what notices: the log line, and the §5 switch
        that tells a consumer the map was actually read (53.116).
        """
        with self.assertNoLogs("app.services.loop_status_service", level="WARNING"):
            body = self.client.get("/api/health").json()

        self.assertTrue(
            body["loop"]["counts"]["predictions_available"],
            "the first poll could not read the predictions map: the calibration "
            "read no longer runs before the counts query",
        )


if __name__ == "__main__":
    unittest.main()
