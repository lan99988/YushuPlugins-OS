from tests.test_core_domains import installed, invoke


def test_invalid_timezone_update_leaves_existing_habit_usable(tmp_path):
    core, _ = installed(tmp_path, "habit")
    first = invoke(core, "habit.create", {"name": "walk", "timezone": "Asia/Shanghai"})["data"]["entity"]
    refused = invoke(core, "habit.update", {"id": first["id"], "expected_version": 1,
                     "changes": {"timezone": "Never/Exists"}}, "bad-zone")
    assert refused["status"] == "failed"
    assert refused["error"]["code"] == "invalid_timezone"
    read = invoke(core, "habit.get", {"id": first["id"]}, "read")["data"]["entity"]
    assert read["version"] == 1 and read["fields"]["timezone"] == "Asia/Shanghai"
    assert invoke(core, "habit.checkin", {"habit_id": first["id"], "date": "2026-10-06"}, "valid-checkin")["status"] == "succeeded"
