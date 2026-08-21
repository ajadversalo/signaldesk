from scan_runs import fail_run, finish_run, latest_runs, start_run


def test_scan_runs_are_persisted_and_latest_status_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(tmp_path / "runs.db"))

    successful = start_run("xsp")
    finish_run(successful, 1)
    failed = start_run("csp")
    fail_run(failed, "provider unavailable")

    runs = latest_runs()
    assert runs["xsp"]["status"] == "SUCCEEDED"
    assert runs["xsp"]["result_count"] == 1
    assert runs["xsp"]["completed_at_utc"] is not None
    assert runs["csp"]["status"] == "FAILED"
    assert runs["csp"]["error"] == "provider unavailable"
