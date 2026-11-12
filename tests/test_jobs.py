"""Review finding 3 (owner "Yes, fix all", 2026-11-12): a job that crashes its worker every time
must not be requeued forever. After the default of 3 crash requeues, the next crash fails it with a
code that says why."""

from __future__ import annotations

from dbig_contracts import jobs


def test_a_job_that_keeps_crashing_fails_after_three_requeues(tmp_path):
    queue = tmp_path / "jobs.sqlite3"
    job_id = jobs.submit(queue, {"bundles": ["b"]})
    for crash in range(1, 5):
        assert jobs.claim(queue) == (job_id, {"bundles": ["b"]}), crash  # the worker takes it
        jobs.requeue_running(queue)  # it crashed; the next start-up puts it back, or gives up
        if crash <= 3:
            assert jobs.status(queue, job_id)["status"] == "queued", crash
    assert jobs.status(queue, job_id)["status"] == "failed"
    assert jobs.status(queue, job_id)["error"] == {"code": "CRASH_LIMIT_REACHED"}
    assert jobs.claim(queue) is None
