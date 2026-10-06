from plugins.feishu import adapter
from tests.test_app_connectors import envelope, binding


def test_calendar_update_mismatched_confirmation_remains_unknown_and_not_reposted(tmp_path):
    fields = {"calendar_id": "cal", "event_id": "expected", "summary": "test", "timezone": "UTC",
              "start_at": "2026-10-06T00:00:00Z", "end_at": "2026-10-06T01:00:00Z"}
    request = envelope(adapter, "feishu.calendar.event.update", fields, tmp_path)
    calls = []
    def provider(*args):
        calls.append(args)
        return {"event": {"event_id": "different", "summary": "test"}}
    first = adapter.handle_envelope(request, binding=binding(adapter), transport=provider)
    assert first["status"] == "unknown"
    assert first["error"]["code"] == "outcome_unknown"
    replay = adapter.handle_envelope(request, binding=binding(adapter), transport=provider)
    assert replay["status"] == "unknown" and len(calls) == 1
