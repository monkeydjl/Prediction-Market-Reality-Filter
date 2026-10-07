"""Which registered scheduler jobs does the suite drive directly?

Measured on the live ``loop_runs`` ledger: 30 rows of ``sport_market_discover``
arrived in ten groups of three, and ``translate_titles`` split 157 real / 3
test-written.  Answering "who wrote these rows?" cost a hand re-derivation both
times.

The first attempt scanned the tests for literal payloads, and that has a **false
negative**: a test that runs the *real* job forges no payload at all, so the scan
cannot see it -- the ten triples came from tests that call the job.  The second
pass (grep each registered job name under ``tests/``) is the one that worked,
and this module makes it permanent.  So the question "could a test have written
this ledger row?" is now answered by the source, not by re-reading a database
that has already been written to.

*The population is derived, not declared.*  The job functions are the first
argument of every ``scheduler.add_job(...)`` call in ``app/core/scheduler.py``,
read with ``ast``.  The other side is derived from ``tests/*.py``, also with
``ast`` -- deliberately not with a text match, because a name in a comment or a
docstring is not a test driving a job, and a regex has no way to tell the
difference.  Only the **exemption table** is written by hand, in the same shape
as ``conftest._RESET_EXEMPT``, ``test_settings_redirect_census._REDIRECT_EXEMPT``
and the scheduler's own ``CORE/OPTIONAL_STARTUP_COMPONENTS``: a partition where
the declared side must keep describing the code.

*Why this is the shape and not a pass/fail list.*  "Every job must have a direct
test" is not the invariant -- several jobs are exercised through the service
they call, and demanding a job-level test for each would be a coverage policy
dressed up as a correctness rule.  What has to stay true is that the two sets
still partition the population and that every exemption carries its reason, so
the next census starts from a derived answer.
"""
import ast
import pathlib

# backend/tests/<this file> -> backend/ -> the checkout root
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCHEDULER = _BACKEND_ROOT / "app" / "core" / "scheduler.py"
_TESTS_DIR = _BACKEND_ROOT / "tests"

# Registered job functions that **no** test file names, each with the reason.
# An entry here is a decision, not a note: while a name is listed, adding a test
# that drives it fails this module until the entry is deleted, and deleting a
# test fails it too.
_NOT_DRIVEN_BY_TESTS: dict[str, str] = {
    "_job_sentiment_refresh": (
        "the only registered job with no direct test. The cache it refreshes is "
        "read through `get_cached_sentiment` in the rule-engine tests, but the "
        "*job* -- its ledger row and its IntervalTrigger(hours=8) registration "
        "-- is driven by nothing in this suite"
    ),
}

# `_job_`-prefixed functions in scheduler.py that are NOT jobs. Named here so the
# population cannot quietly absorb them: the census below reads only add_job's
# first argument, and this control proves that is what happens.
_JOB_PREFIXED_NON_JOBS = {"_job_name_for_run"}


def _registered_job_functions(path: pathlib.Path) -> dict[str, str]:
    """``{function_name: job_id}`` for every ``scheduler.add_job(...)`` call.

    ``add_job`` takes the callable positionally (``add_job(_job_x, trigger,
    id="x")``), so the first argument is either a ``Name`` (a module-level
    function) or an ``Attribute``.  A trigger built inline is fine -- nothing
    here evaluates it, and nothing here runs the scheduler.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_job"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            name = first.id
        elif isinstance(first, ast.Attribute):
            name = first.attr
        else:
            continue
        job_id = "<unresolved>"
        for keyword in node.keywords:
            if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                job_id = str(keyword.value.value)
        found[name] = job_id
    return found


def _identifiers_in(sources) -> set[str]:
    """Every name a test file *refers to*, via AST.

    Attribute access counts by its attribute (``scheduler._job_x`` -> the test
    IS driving the job), and an import counts by its bound name.  Strings,
    comments and docstrings do not count, which is the whole reason this is not
    a regex.
    """
    names: set[str] = set()
    for text in sources:
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.alias):
                names.add(node.name.rsplit(".", 1)[-1])
                if node.asname:
                    names.add(node.asname)
    return names


def _test_sources() -> list[str]:
    return [
        path.read_text(encoding="utf-8-sig", errors="replace")
        for path in sorted(_TESTS_DIR.glob("*.py"))
    ]


class TestTheDerivationItself:
    """Controls first: a census that cannot say no proves nothing."""

    def test_the_population_is_derived_and_real(self):
        registered = _registered_job_functions(_SCHEDULER)
        assert len(registered) >= 20, sorted(registered)
        for known in (
            "_job_event_discover",
            "_job_translate_titles",
            "_job_loop_run_ledger_maintenance",
            "_job_sentiment_refresh",
        ):
            assert known in registered, f"{known} missing from {sorted(registered)}"

    def test_a_job_prefixed_helper_is_not_a_job(self):
        """`_job_name_for_run` starts with `_job_` and is not registered.

        A census that matched the prefix instead of add_job's argument would
        already be wrong on this repository.
        """
        registered = _registered_job_functions(_SCHEDULER)
        for name in _JOB_PREFIXED_NON_JOBS:
            assert name not in registered, (
                f"{name} is not a job but the census picked it up; the population "
                "must come from add_job's first argument"
            )

    def test_the_identifier_walk_sees_calls_and_ignores_prose(self):
        blob = (
            "import app.core.scheduler as scheduler\n"
            "scheduler._job_alpha()\n"
            "_job_beta()\n"
            "from app.core.scheduler import _job_gamma\n"
            "PROSE = 'scheduler._job_delta() is described here'\n"
            '"""_job_epsilon is only a docstring."""\n'
            "# _job_zeta is only a comment\n"
        )
        names = _identifiers_in([blob])
        assert {"_job_alpha", "_job_beta", "_job_gamma"} <= names, sorted(names)
        for prose_only in ("_job_delta", "_job_epsilon", "_job_zeta"):
            assert prose_only not in names, (
                f"{prose_only} is only ever text; counting it would make a "
                "comment look like a test that drives a job"
            )

    def test_the_population_side_can_say_no(self):
        """A name that is not in the file must not be reported as registered."""
        registered = _registered_job_functions(_SCHEDULER)
        assert "not_a_job_at_all" not in registered


class TestEveryRegisteredJobIsClassified:
    def test_the_undriven_set_is_exactly_the_declared_one(self):
        registered = _registered_job_functions(_SCHEDULER)
        referenced = _identifiers_in(_test_sources())
        undriven = sorted(name for name in registered if name not in referenced)

        assert referenced & set(registered), (
            "no test file refers to any registered job; the scan or the test "
            "layout changed, and this assertion would otherwise be vacuous"
        )
        assert undriven == sorted(_NOT_DRIVEN_BY_TESTS), (
            "the set of registered jobs no test names changed. If a test now "
            "drives one of the listed jobs, delete its entry; if a job was just "
            "registered, add it with the reason it has no direct test.\n"
            f"  undriven by the scan : {undriven}\n"
            f"  declared undriven    : {sorted(_NOT_DRIVEN_BY_TESTS)}"
        )

    def test_the_exemption_table_is_not_stale(self):
        registered = _registered_job_functions(_SCHEDULER)
        unknown = sorted(set(_NOT_DRIVEN_BY_TESTS) - set(registered))
        assert unknown == [], (
            f"declared undriven but not a registered job: {unknown} -- the job "
            "was renamed or removed and the entry is now noise"
        )
        for name, reason in _NOT_DRIVEN_BY_TESTS.items():
            assert reason.strip(), f"{name} is exempted with no reason written"
