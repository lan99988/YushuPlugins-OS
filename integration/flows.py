from hashlib import sha256
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from urllib.parse import quote


class FlowError(RuntimeError):
    def __init__(self, result):
        self.result = result.to_dict() if hasattr(result, "to_dict") else result
        super().__init__("flow_step_not_confirmed")


def reference(provider, kind, resource_id, store_id):
    return {"provider": provider, "kind": kind, "id": resource_id, "store_id": store_id}


def uri(ref):
    return "yushuos://" + ref["provider"] + "/" + "/".join(quote(ref[k], safe="") for k in ("store_id", "kind", "id"))


class Flows:
    def __init__(self, core, stores, run_id):
        self.core, self.stores, self.run_id = core, dict(stores), run_id

    def call(self, step, capability, fields, *, intent=None, target=None):
        slug = capability.split(".")[0].replace("-", "_")
        project_ref = self.core.config.get("project_ref", "")
        request_id = "flow_" + sha256(json.dumps([project_ref,self.run_id,step],ensure_ascii=False).encode()).hexdigest()
        if intent is None:
            providers = self.core.registry.providers(capability)
            intents = {item.capability.intents[0] for item in providers if item.capability.intents}
            if len(intents) != 1:
                raise ValueError("intent_required")
            intent = intents.pop()
        if target is None:
            target = {"store_id": self.stores[slug]}
            if slug == "task" and fields.get("task_id"):
                target["task_id"] = fields["task_id"]
        request = {"request_id": request_id, "capability": capability, "intent": intent, "fields": fields,
                   "target": target, "project_ref": project_ref}
        result = self.core.invoke(request, mode="execute", host_mode="execute")
        if result.status == "unknown":
            result = self.core.resume(request, host_mode="execute")
        if result.status != "succeeded":
            raise FlowError(result)
        return result.to_dict()["data"]

    def capture_to(self, capture_id, destination):
        source = self.call("read_source", "capture.get", {"id": capture_id})["entity"]
        if source["fields"].get("status") == "processed":
            return source["fields"].get("target_ref")
        if destination not in {"task", "idea", "bug"}:
            raise ValueError("unsupported_destination")
        fields = {"title": source["fields"]["content"][:500]}
        if destination == "task":
            fields["source_ref"] = uri(reference("yushuos.capture", "capture", capture_id, self.stores["capture"]))
        data = self.call("create_target", destination + ".create", fields)
        entity = data["task"] if destination == "task" else data["entity"]
        ref = reference("yushuos." + destination, destination, entity["id"], self.stores[destination])
        self.call("confirm_source", "capture.mark_processed", {"id": capture_id, "expected_version": source["version"], "target_ref": ref})
        return ref

    def convert_to_task(self, source_slug, source_id):
        if source_slug not in {"idea", "bug"}:
            raise ValueError("unsupported_source")
        source = self.call("read_source", source_slug + ".get", {"id": source_id})["entity"]
        if source["fields"].get("target_ref"):
            return source["fields"]["target_ref"]
        task = self.call("create_target", "task.create", {"title": source["fields"]["title"],
                         "source_ref": uri(reference("yushuos." + source_slug, source_slug, source_id, self.stores[source_slug]))})["task"]
        ref = reference("yushuos.task", "task", task["id"], self.stores["task"])
        self.call("confirm_source", source_slug + (".convert" if source_slug == "idea" else ".link_task"),
                  {"id": source_id, "expected_version": source["version"], "target_ref": ref})
        return ref

    def plan_from_tasks(self, windows, energy_budget_minutes=None):
        tasks, cursor = [], None
        page_index = 0
        while True:
            fields = {"limit": 100, "status": ["open"]}
            if cursor:
                fields["cursor"] = cursor
            page = self.call("tasks_" + str(page_index), "task.list", fields)
            tasks.extend(page["tasks"])
            cursor = page["next_cursor"]
            if not cursor:
                break
            page_index += 1
        body = self.call("body", "body.state.get_latest", {})["entity"]
        inputs = {"tasks": [{k: t[k] for k in ("id", "version", "priority", "estimate_minutes", "due_at") if t.get(k) is not None}
                            for t in tasks if t.get("estimate_minutes")], "windows": windows}
        # Body facts are returned to the host; no invented conversion from energy to time.
        if energy_budget_minutes is not None:
            inputs["energy_budget_minutes"] = energy_budget_minutes
        plan = self.call("generate", "planner.generate", inputs)["entity"]
        return {"plan": plan, "body": body, "missing_body": body is None,
                "missing_duration": [t["id"] for t in tasks if not t.get("estimate_minutes")]}

    def apply_plan(self, plan_id):
        plan = self.call("read_plan", "planner.plan.get", {"id": plan_id})["entity"]
        for index, item in enumerate(plan["fields"]["scheduled"]):
            task = self.call("verify_task_" + str(index), "task.get", {"task_id": item["id"]})["task"]
            if task["version"] != item["task_version"] or task["status"] != "open":
                raise FlowError({"status": "failed", "error": {"code": "plan_drift"}})
        return self.call("apply", "planner.apply", {"id": plan_id, "expected_version": plan["version"],
                                                      "input_digest": plan["fields"]["input_digest"]})

    def task_review(self, period_start, period_end):
        tasks, cursor, i = [], None, 0
        while True:
            fields = {"limit": 100, "include_archived": True, "include_deleted": False}
            if cursor:
                fields["cursor"] = cursor
            data = self.call("review_tasks_" + str(i), "task.list", fields)
            tasks.extend(data["tasks"])
            cursor = data["next_cursor"]
            if not cursor:
                break
            i += 1
        selected = [t for t in tasks if period_start <= t["created_at"][:10] <= period_end]
        refs = [reference("yushuos.task", "task", t["id"], self.stores["task"]) for t in selected]
        return self.call("review", "review.generate", {"period_start": period_start, "period_end": period_end,
                        "sources": {"task": {"planned": len(selected), "completed": sum(t["status"] == "completed" for t in selected)}},
                        "missing_sources": ["finance", "habit", "body"], "source_refs": refs})

    def sync_plan_calendar(self, plan_id, calendar_id, timezone):
        """Explicit external side effects, stable step IDs; never auto-apply a plan."""
        plan = self.call("calendar_plan", "planner.plan.get", {"id": plan_id})["entity"]
        if plan["fields"].get("status") != "applied":
            raise FlowError({"status": "failed", "error": {"code": "plan_not_applied"}})
        references = []
        for index, item in enumerate(plan["fields"]["scheduled"]):
            task = self.call("calendar_task_" + str(index), "task.get", {"task_id": item["id"]})["task"]
            if task["version"] != item["task_version"] or task["status"] != "open":
                raise FlowError({"status": "failed", "error": {"code": "plan_drift"}})
            data = self.call("calendar_create_" + str(index), "feishu.calendar.event.create",
                             {"calendar_id": calendar_id, "summary": task["title"], "timezone": timezone,
                              "start_at": item["start"], "end_at": item["end"]}, target={"calendar_id": calendar_id})
            references.append(reference("yushuos.feishu", "calendar.event", data["provider_id"], calendar_id))
        return references

    def knowledge_to_ima(self, knowledge_id, folder_id):
        source = self.call("knowledge_read", "knowledge.get", {"id": knowledge_id})["entity"]
        existing = source["fields"].get("ima_ref")
        if existing:
            return existing
        data = self.call("ima_create", "ima.note.create", {"folder_id": folder_id,
                         "content": source["fields"].get("content") or source["fields"]["title"]}, target={"folder_id": folder_id})
        ref = reference("yushuos.ima", "note", data["provider_id"], folder_id)
        self.call("knowledge_confirm", "knowledge.update", {"id": knowledge_id,
                  "expected_version": source["version"], "changes": {"ima_ref": ref}})
        return ref

    def ima_to_knowledge(self, note_id):
        data = self.call("ima_read", "ima.note.get", {"note_id": note_id}, target={"note_id": note_id})
        note = data["entity"]
        ref = reference("yushuos.ima", "note", note["id"], note_id)
        return self.call("knowledge_import", "knowledge.create", {"title": note.get("title") or "IMA note",
                         "content": note.get("content", ""), "ima_ref": ref})["entity"]

    def period_review(self, period_start, period_end, *, currency, habit_expected):
        """Collect through Core; state-record UTC dates and explicit habit denominators."""
        start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
        if start > end or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in habit_expected.values()):
            raise ValueError("invalid_review_window_or_denominator")
        sources, refs, missing, metadata = {}, [], [], {}
        def pages(label, cap, fields, key="items"):
            values, cursor, index = [], None, 0
            while True:
                query = {**fields, "limit": 100}
                if cursor:
                    query["cursor"] = cursor
                result = self.call(label + "_" + str(index), cap, query)
                values.extend(result[key])
                cursor = result["next_cursor"]
                if not cursor:
                    return values
                index += 1
        tasks = pages("period_tasks", "task.list", {"include_archived": True}, "tasks") if "task" in self.stores else []
        tasks = [t for t in tasks if period_start <= t["created_at"][:10] <= period_end]
        if "task" in self.stores:
            sources["task"] = {"planned": len(tasks), "completed": sum(t["status"] == "completed" for t in tasks)}
        else:
            missing.append("task")
        refs.extend(reference("yushuos.task", "task", t["id"], self.stores["task"]) for t in tasks)
        states = pages("period_body", "body.state.history", {}) if "body" in self.stores else []
        states = [s for s in states if start <= datetime.fromisoformat(s["fields"]["at"]).astimezone(timezone.utc).date() <= end]
        if "body" in self.stores:
            sources["body"] = {"records": len(states)}
        else:
            missing.append("body")
        refs.extend(reference("yushuos.body", "body.state", s["id"], self.stores["body"]) for s in states)
        missing.extend(["body.sleep_metrics", "body.training_metrics"])
        total = 0
        for index, (habit_id, expected) in enumerate(sorted(habit_expected.items())):
            checkins = pages("period_habit_" + str(index), "habit.history", {"habit_id": habit_id})
            checkins = [h for h in checkins if period_start <= h["fields"]["date"] <= period_end and not h["fields"].get("undone")]
            if len(checkins) > expected:
                raise ValueError("checkins_exceed_explicit_expected")
            total += len(checkins)
            refs.extend(reference("yushuos.habit", "habit.checkin", h["id"], self.stores["habit"]) for h in checkins)
        if habit_expected:
            sources["habit"] = {"expected": sum(habit_expected.values()), "checkins": total}
        else:
            missing.append("habit")
        if "finance" in self.stores and start.day == 1 and start.year == end.year and start.month == end.month and end.day == monthrange(end.year, end.month)[1]:
            summary = self.call("period_finance", "finance.summary", {"month": period_start[:7], "currency": currency})
            amounts = {kind + "_minor": int(Decimal(summary[kind]) * 100) for kind in ("income", "expense")}
            if any(value > 9007199254740991 for value in amounts.values()):
                raise ValueError("review_minor_amount_out_of_range")
            sources["finance"] = {"statements": len(summary["statement_ids"]), **amounts}
            metadata["finance_context"] = {"currency": currency, "month": period_start[:7], "unit": "minor"}
            refs.extend(reference("yushuos.finance", "finance.statement", rid, self.stores["finance"]) for rid in summary["statement_ids"])
        else:
            missing.append("finance")  # Monthly inputs cannot establish daily/weekly totals.
        return self.call("period_review", "review.generate", {"period_start": period_start, "period_end": period_end,
                         "sources": sources, "missing_sources": missing, "source_refs": refs, **metadata})
