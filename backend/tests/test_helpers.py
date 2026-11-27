"""Tests for ``app.utils.helpers``: ``as_utc`` and the serialisers built on it.

The helper exists because five serializers were each doing ``.isoformat()`` on a
datetime SQLite had handed back naive, and a browser's ``new Date(...)`` reads
the resulting marker-less string as local time. Its two hard cases are the ones
those call sites never exercised: an aware value with a non-UTC offset (must be
converted, not relabelled) and ``None`` (must stay ``None``).

``as_utc`` was split out of ``utc_isoformat`` in audit 53.183, when the row->dict
converters started calling it: they keep the ``datetime`` (the response model
declares one), so the type and the wall clock are part of the contract, not an
implementation detail of the string.
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.utils.helpers import as_utc, utc_isoformat, utc_now


class AsUtcTests(unittest.TestCase):
    def test_naive_keeps_its_wall_clock_and_gains_utc(self):
        out = as_utc(datetime(2026, 6, 20, 12, 0, 0))
        assert out is not None
        self.assertIsInstance(out, datetime)
        self.assertEqual(out, datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(out.utcoffset(), timedelta(0))

    def test_aware_offset_is_converted_to_utc(self):
        """12:00 at +08:00 is 04:00 UTC, and the *type* stays ``datetime``."""
        cst = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(
            as_utc(cst), datetime(2026, 6, 20, 4, 0, 0, tzinfo=timezone.utc)
        )

    def test_none_stays_none(self):
        self.assertIsNone(as_utc(None))


class UtcIsoformatTests(unittest.TestCase):
    def test_naive_is_labelled_utc(self):
        """The case every call site hit: a stored value with no tzinfo."""
        self.assertEqual(
            utc_isoformat(datetime(2026, 6, 20, 12, 0, 0)),
            "2026-06-20T12:00:00+00:00",
        )

    def test_aware_utc_keeps_its_instant(self):
        self.assertEqual(
            utc_isoformat(datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)),
            "2026-06-20T12:00:00+00:00",
        )

    def test_aware_offset_is_converted_not_relabelled(self):
        """12:00 at +08:00 is 04:00 UTC.

        ``.replace(tzinfo=timezone.utc)`` would keep the wall clock and move the
        instant by eight hours -- the same error the helper removes, with the
        sign flipped. A driver that returns an offset (Postgres, MySQL) must
        therefore go through ``astimezone``.
        """
        cst = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(utc_isoformat(cst), "2026-06-20T04:00:00+00:00")

    def test_none_stays_none(self):
        """`no timestamp` and `the epoch` are different facts."""
        self.assertIsNone(utc_isoformat(None))

    def test_utc_now_carries_an_offset(self):
        """The sibling helper has to satisfy the same contract it feeds."""
        self.assertTrue(utc_now().endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
