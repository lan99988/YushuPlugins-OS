from integration.flows import Flows
from tests.test_system_flows import suite


def test_finance_habit_body_review_uses_explicit_window_denominator_and_sources(tmp_path):
    core, stores = suite(tmp_path, ["task", "finance", "habit", "body", "review"])
    create = Flows(core, stores, "seed")
    create.call("task", "task.create", {"title": "one"})
    create.call("finance", "finance.statement.create", {"month": "2026-10", "currency": "CNY", "source_id": "bill",
                "transactions": [{"amount": "100.00", "direction": "income"}, {"amount": "25.30", "direction": "expense"}]})
    habit = create.call("habit", "habit.create", {"name": "walk", "timezone": "Asia/Shanghai"})["entity"]
    create.call("checkin", "habit.checkin", {"habit_id": habit["id"], "date": "2026-10-06"})
    create.call("body", "body.state.record", {"at": "2026-10-06T00:00:00Z", "source": "manual", "energy": 5})
    create.call("outside-body", "body.state.record", {"at": "2026-10-01T03:00:00+08:00", "source": "manual", "energy": 4})
    result = Flows(core, stores, "monthly").period_review("2026-10-01", "2026-10-31", currency="CNY", habit_expected={habit["id"]: 2})["entity"]
    fields = result["fields"]
    assert fields["sources"]["finance"] == {"statements": 1, "income_minor": 10000, "expense_minor": 2530}
    assert fields["sources"]["habit"] == {"expected": 2, "checkins": 1}
    assert fields["sources"]["body"]["records"] == 1
    assert fields["metrics"]["habit_checkin_rate"] == .5
    assert {r["provider"] for r in fields["source_refs"]} == {"yushuos.task", "yushuos.finance", "yushuos.habit", "yushuos.body"}
    weekly = Flows(core, stores, "weekly").period_review("2026-10-01", "2026-10-07", currency="CNY", habit_expected={})["entity"]
    assert "finance" in weekly["fields"]["missing_sources"] and "finance" not in weekly["fields"]["sources"]


def test_no_installed_domain_does_not_invent_source_metrics(tmp_path):
    core, stores = suite(tmp_path, ["review"])
    review = Flows(core, stores, "missing").period_review("2026-10-01", "2026-10-07", currency="CNY", habit_expected={})["entity"]
    assert review["fields"]["sources"] == {}
    assert {"task", "habit", "finance", "body"} <= set(review["fields"]["missing_sources"])


def test_monthly_review_accepts_amounts_above_ten_thousand_major_units(tmp_path):
    core, stores = suite(tmp_path, ["finance", "review"])
    flow = Flows(core, stores, "large-month")
    flow.call("statement", "finance.statement.create", {"month": "2026-10", "currency": "CNY", "source_id": "large-bill",
              "transactions": [{"amount": "10001.00", "direction": "income"}, {"amount": "10002.30", "direction": "expense"}]})
    result = flow.period_review("2026-10-01", "2026-10-31", currency="CNY", habit_expected={})["entity"]
    assert result["fields"]["sources"]["finance"] == {"statements": 1, "income_minor": 1000100, "expense_minor": 1000230}
