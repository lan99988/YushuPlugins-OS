"""Domain operations. No network, model calls, or access to other plugin stores."""
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .store import DomainError, encoded, now


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise DomainError("invalid_timestamp") from None


def evidence(value):
    if not isinstance(value, list) or not value:
        raise DomainError("evidence_required")
    for ref in value:
        if not isinstance(ref, dict) or not all(isinstance(ref.get(k), str) and ref[k]
                                               for k in ("provider", "kind", "id", "store_id")):
            raise DomainError("invalid_reference")
    return value


def money(value):
    try:
        if not isinstance(value, str):
            raise InvalidOperation()
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            raise InvalidOperation()
        return amount
    except InvalidOperation:
        raise DomainError("invalid_amount") from None


def schedule(fields):
    """Deterministic first-fit; hard constraints precede the explicit priority order."""
    windows = sorted([(timestamp(w["start"]), timestamp(w["end"])) for w in fields["windows"]])
    for index, (start, end) in enumerate(windows):
        if end <= start or (index and start < windows[index - 1][1]):
            raise DomainError("invalid_window")
    ranks = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    tasks = fields["tasks"]
    ids = [task["id"] for task in tasks]
    if len(set(ids)) != len(ids):
        raise DomainError("duplicate_task")
    ordered = sorted(tasks, key=lambda t: (ranks[t.get("priority", "normal")],
                                           t.get("due_at") or "9999", t["id"]))
    scheduled, skipped = [], []
    budget = fields.get("energy_budget_minutes", sum(int((b-a).total_seconds()/60) for a,b in windows))
    for task in ordered:
        duration = task.get("estimate_minutes")
        if type(duration) is not int or duration < 1:
            skipped.append({"id": task["id"], "reason": "missing_duration"})
            continue
        if duration > budget:
            skipped.append({"id": task["id"], "reason": "energy_budget"})
            continue
        from datetime import timedelta
        fitted = False
        for index, (start, end) in enumerate(windows):
            earliest = timestamp(task["earliest_start"]) if task.get("earliest_start") else start
            actual = max(start, earliest)
            finish = actual + timedelta(minutes=duration)
            due = timestamp(task["due_at"]) if task.get("due_at") else end
            if finish <= min(end, due):
                scheduled.append({"id": task["id"], "start": actual.isoformat(), "end": finish.isoformat(),
                                  "task_version": task.get("version", 1)})
                windows[index] = (finish, end)
                budget -= duration
                fitted = True
                break
        if not fitted:
            skipped.append({"id": task["id"], "reason": "no_feasible_window"})
    return {"scheduled": scheduled, "unscheduled": skipped,
            "input_digest": hashlib.sha256(encoded(fields).encode()).hexdigest()}


class DomainService:
    def __init__(self, slug, store):
        self.slug, self.store = slug, store

    def _required(self, kind, scope, entity_id, db):
        entity = self.store.get(kind, scope, entity_id, db)
        if entity is None or entity["fields"].get("status") == "deleted":
            raise DomainError("entity.not_found")
        return entity

    def _write_result(self, entity, changed, scope, event):
        data = {"entity": entity, "changed": changed,
                "operation_status": "committed", "result_state": "available"}
        refs = {"provider": "yushuos." + self.slug, "kind": entity["kind"],
                "id": entity["id"], "store_id": scope}
        uri = "yushuos://" + refs["provider"] + "/" + "/".join(quote(refs[k], safe="") for k in ("store_id", "kind", "id"))
        if len(uri) > 512:
            raise DomainError("invalid_reference")
        return data, [{"type": event, "resource_refs": {"plugin_id": refs["provider"], "id": refs["id"],
                                                        "resource_type": refs["kind"], "ref": uri}}] if changed else []

    def operate(self, capability, fields, scope, db=None):
        if not capability.startswith(self.slug + "."):
            raise DomainError("capability_unavailable")
        kind, action = capability.rsplit(".", 1)
        if self.slug == "personal_model" and kind == self.slug:
            kind = "personal_model.hypothesis"
        if self.slug == "cognition" and kind == self.slug:
            kind = "cognition.pattern"
        if self.slug == "finance":
            return self._finance(kind, action, fields, scope, db)
        if self.slug == "habit" and action in {"checkin", "undo_checkin", "history"}:
            return self._habit(action, fields, scope, db)
        if self.slug == "deepwork" and action in {"start", "pause", "resume", "finish", "cancel", "interrupt"}:
            return self._deepwork(action, fields, scope, db)
        if self.slug == "planner" and action in {"generate", "preview", "apply", "replan", "suggest"}:
            return self._planner(action, fields, scope, db)
        if self.slug == "review":
            return self._review(action, fields, scope, db)
        if self.slug == "body" and action in {"get_latest", "summary"}:
            values = self.store.list("body.state", scope, db, include_archived=True)
            values.sort(key=lambda e: (e["fields"]["at"], e["id"]))
            if action == "get_latest":
                return {"entity": values[-1] if values else None}, []
            latest = values[-1]["fields"] if values else {}
            return {"summary": {k: latest.get(k) for k in ("energy", "fatigue", "recovery")},
                    "missing": [k for k in ("energy", "fatigue", "recovery") if k not in latest]}, []
        if self.slug == "relationship" and action == "recent":
            self._required("relationship.person", scope, fields["person_id"], db)
            values = self.store.list("relationship.interaction", scope, db,
                                     filters={"person_id": fields["person_id"]})
            values.sort(key=lambda e: (e["fields"]["at"], e["id"]))
            return {"entity": values[-1] if values else None}, []
        if action in {"create", "record", "start"}:
            values = dict(fields)
            if kind == "planner.plan":
                values.update(schedule(fields), status="draft")
            if self.slug == "habit":
                try:
                    ZoneInfo(values["timezone"])
                except (KeyError, ValueError):
                    raise DomainError("invalid_timezone") from None
            if self.slug == "relationship" and kind == "relationship.interaction":
                self._required("relationship.person", scope, values["person_id"], db)
            if self.slug == "knowledge" and kind in {"knowledge.concept", "knowledge.link", "knowledge.insight"}:
                self._required("knowledge", scope, values["knowledge_id"], db)
            if kind.endswith("hypothesis") and values.get("confidence", 0) > 0 and not values.get("evidence_refs"):
                values["confidence_note"] = "unvalidated_host_estimate"
            defaults = {"capture": "pending", "idea": "active", "bug": "open", "habit": "active",
                        "lifeadmin": "open", "decision": "open", "creation": "idea", "competition": "active"}
            if kind == self.slug and self.slug in defaults:
                values["status"] = defaults[self.slug]
            if self.slug == "body":
                timestamp(values["at"])
            if self.slug == "relationship" and kind == "relationship.interaction":
                timestamp(values["at"])
            entity = self.store.create(db, kind, scope, values)
            return self._write_result(entity, True, scope, kind + ".created")
        if action == "get":
            return {"entity": self._required(kind, scope, fields["id"], db)}, []
        if action in {"list", "history", "search", "changes"}:
            values = self.store.list(kind, scope, db, fields.get("include_archived", False), fields.get("filters"))
            if fields.get("query"):
                query = fields["query"].casefold()
                values = [e for e in values if query in encoded(e["fields"]).casefold()]
            values = [e for e in values if e["fields"].get("status") != "deleted"]
            cursor = fields.get("cursor")
            if cursor:
                indexes = [i for i, e in enumerate(values) if e["id"] == cursor]
                if not indexes:
                    raise DomainError("invalid_cursor")
                values = values[indexes[0] + 1:]
            limit = fields.get("limit", 50)
            page = values[:limit]
            return {"items": page, "next_cursor": page[-1]["id"] if len(values) > limit else None}, []
        entity = self._required(kind, scope, fields["id"], db)
        changes, archive = {}, None
        if action == "update":
            if entity["fields"].get("status") in {"completed", "closed", "discarded"}:
                raise DomainError("invalid_transition")
            changes = fields["changes"]
        elif action in {"archive", "restore"}:
            archive = action == "archive"
        elif action == "delete":
            changes = {"status": "deleted"}
        elif action == "route":
            if entity["fields"].get("status") != "pending":
                raise DomainError("invalid_transition")
            changes = {"classification": fields["classification"]}
        elif action in {"mark_processed", "convert", "link_task"}:
            ref = fields.get("target_ref")
            if not ref and not fields.get("ignored_reason"):
                raise DomainError("evidence_required")
            if ref:
                evidence([ref])
            changes = {"target_ref": ref}
            if action == "mark_processed":
                if entity["fields"].get("status") not in {"pending", "processed"}:
                    raise DomainError("invalid_transition")
                changes.update(status="processed", ignored_reason=fields.get("ignored_reason"))
            elif action == "convert":
                changes["status"] = "converted"
        elif action in {"evaluate", "analyze", "review"}:
            refs = fields.get("evidence_refs") or entity["fields"].get("evidence_refs")
            evidence(refs)
            if not fields.get("reason") or not fields.get("judgement"):
                raise DomainError("host_judgement_required")
            changes = {"evaluation": {"evidence_refs": refs, "reason": fields["reason"],
                                      "judgement": fields["judgement"], "at": now()}}
            if "confidence" in fields:
                changes["confidence"] = fields["confidence"]
        elif action == "attach":
            ref = evidence([fields["reference"]])[0]
            refs = list(entity["fields"].get("evidence_refs", []))
            if ref not in refs:
                refs.append(ref)
            changes = {"evidence_refs": refs}
        elif action == "choose":
            if fields["option_id"] not in [o["id"] for o in entity["fields"].get("options", [])]:
                raise DomainError("invalid_option")
            changes = {"chosen_option": fields["option_id"], "reason": fields["reason"], "status": "chosen"}
        elif action in {"outcome", "renew", "progress", "milestone", "publication", "advance_stage"}:
            return self._record_action(action, fields, entity, kind, scope, db)
        else:
            transitions = {"resolve": ({"open", "resolved"}, "resolved"),
                           "reopen": ({"resolved", "closed", "completed", "cancelled"}, "open"),
                           "close": ({"resolved", "closed"}, "closed"),
                           "pause": ({"active", "paused"}, "paused"),
                           "resume": ({"paused", "active"}, "active"),
                           "complete": ({"open", "completed"}, "completed"),
                           "discard": ({"active", "discarded"}, "discarded"),
                           "withdraw": ({"active", "withdrawn"}, "withdrawn")}
            if action not in transitions:
                raise DomainError("capability_unavailable")
            allowed, status = transitions[action]
            if entity["fields"].get("status", "active") not in allowed:
                raise DomainError("invalid_transition")
            changes = {"status": status}
        result, changed = self.store.update(db, kind, scope, entity["id"], fields["expected_version"],
                                            changes, archived=archive)
        return self._write_result(result, changed, scope, kind + "." + action)

    def _record_action(self, action, fields, entity, kind, scope, db):
        payload = {k: v for k, v in fields.items() if k not in {"id", "expected_version"}}
        values = entity["fields"]
        if action == "advance_stage":
            stages = ["idea", "draft", "review", "ready"]
            current, target = values.get("status", "idea"), fields["stage"]
            if current not in stages or target not in stages or stages.index(target) != stages.index(current) + 1:
                raise DomainError("invalid_transition")
            changes = {"status": target}
        elif action == "publication":
            if values.get("status") != "ready":
                raise DomainError("invalid_transition")
            changes = {"publication": payload, "status": "published"}
        elif action == "renew":
            date.fromisoformat(fields["due_date"])
            changes = {"due_date": fields["due_date"], "status": "open",
                       "renewals": [*values.get("renewals", []), payload]}
        else:
            key = {"outcome": "outcomes", "milestone": "milestones", "progress": "progress"}[action]
            record_id = fields["record_id"]
            previous = values.get(key, [])
            old = next((v for v in previous if v["record_id"] == record_id), None)
            if old and old != payload:
                raise DomainError("duplicate_record_conflict")
            changes = {key: previous if old else [*previous, payload]}
        result, changed = self.store.update(db, kind, scope, entity["id"], fields["expected_version"], changes)
        return self._write_result(result, changed, scope, kind + "." + action)

    def _habit(self, action, fields, scope, db):
        habit = self._required("habit", scope, fields["habit_id"], db)
        if action == "history":
            return {"items": self.store.list("habit.checkin", scope, db, True,
                                             {"habit_id": habit["id"]}), "next_cursor": None}, []
        day = date.fromisoformat(fields["date"])
        ZoneInfo(habit["fields"]["timezone"])
        if habit["fields"].get("status") != "active":
            raise DomainError("invalid_transition")
        entity_id = "checkin_" + hashlib.sha256((habit["id"] + str(day)).encode()).hexdigest()
        entity = self.store.get("habit.checkin", scope, entity_id, db)
        if action == "checkin":
            if entity:
                result, changed = self.store.update(db, "habit.checkin", scope, entity_id, entity["version"],
                                                    {"undone": False})
            else:
                result = self.store.create(db, "habit.checkin", scope,
                                           {"habit_id": habit["id"], "date": str(day), "undone": False}, entity_id)
                changed = True
        else:
            if not entity:
                raise DomainError("entity.not_found")
            result, changed = self.store.update(db, "habit.checkin", scope, entity_id, entity["version"], {"undone": True})
        return self._write_result(result, changed, scope, "habit." + action)

    def _finance(self, kind, action, fields, scope, db):
        if action in {"get", "list", "summary"}:
            if action == "get":
                return {"entity": self._required(kind, scope, fields["id"], db)}, []
            items = self.store.list(kind if action == "list" else "finance.statement", scope, db,
                                    filters=fields.get("filters"))
            if action == "list":
                return {"items": items[:fields.get("limit", 50)], "next_cursor": None}, []
            items = [e for e in items if e["fields"]["month"] == fields["month"]
                     and e["fields"]["currency"] == fields["currency"]]
            return {"month": fields["month"], "currency": fields["currency"],
                    "income": str(sum((Decimal(e["fields"]["income"]) for e in items), Decimal("0.00"))),
                    "expense": str(sum((Decimal(e["fields"]["expense"]) for e in items), Decimal("0.00"))),
                    "statement_ids": [e["id"] for e in items]}, []
        if action == "close":
            entity, changed = self.store.update(db, kind, scope, fields["id"], fields["expected_version"], {"closed": True})
            return self._write_result(entity, changed, scope, "finance.month.closed")
        date.fromisoformat(fields["month"] + "-01")
        values = dict(fields)
        existing = self.store.list(kind, scope, db, True)
        match = next((e for e in existing if e["fields"].get("source_id") == fields["source_id"]), None)
        if match:
            original = {k: match["fields"][k] for k in fields}
            if original != fields:
                raise DomainError("duplicate_source_conflict")
            return self._write_result(match, False, scope, kind + ".created")
        if kind == "finance.statement":
            income, expense = Decimal("0.00"), Decimal("0.00")
            for transaction in fields["transactions"]:
                value = money(transaction["amount"])
                if transaction["direction"] == "income":
                    income += value
                elif transaction["direction"] == "expense":
                    expense += value
                else:
                    raise DomainError("invalid_direction")
            values.update(income=str(income), expense=str(expense))
        else:
            values["closed"] = False
            for key in ("income", "expense", "assets", "liabilities"):
                values[key] = str(money(values[key]))
        entity = self.store.create(db, kind, scope, values)
        return self._write_result(entity, True, scope, kind + ".created")

    def _deepwork(self, action, fields, scope, db):
        at = timestamp(fields.get("at", now()))
        if action == "start":
            entity = self.store.create(db, "deepwork", scope,
                {**fields, "status": "running", "active_seconds": 0, "started_at": at.isoformat(), "last_at": at.isoformat()})
            return self._write_result(entity, True, scope, "deepwork.started")
        entity = self._required("deepwork", scope, fields["id"], db)
        values = entity["fields"]
        if at < timestamp(values["last_at"]):
            raise DomainError("time_reversal")
        transitions = {"pause": ({"running"}, "paused"), "resume": ({"paused"}, "running"),
                       "finish": ({"running", "paused"}, "finished"), "cancel": ({"running", "paused"}, "cancelled"),
                       "interrupt": ({"running"}, "running")}
        allowed, target = transitions[action]
        if values["status"] not in allowed:
            raise DomainError("invalid_transition")
        elapsed = int((at - timestamp(values["last_at"])).total_seconds()) if values["status"] == "running" else 0
        changes = {"status": target, "last_at": at.isoformat(), "active_seconds": values["active_seconds"] + elapsed}
        if action == "interrupt":
            changes["interruptions"] = [*values.get("interruptions", []), {"at": at.isoformat(), "reason": fields["reason"]}]
        entity, changed = self.store.update(db, "deepwork", scope, entity["id"], fields["expected_version"], changes)
        return self._write_result(entity, changed, scope, "deepwork." + action)

    def _planner(self, action, fields, scope, db):
        if action in {"preview", "suggest"}:
            planned = schedule(fields)
            if action == "suggest":
                planned["scheduled"] = planned["scheduled"][:1]
            return {"plan": planned}, []
        if action == "generate":
            entity = self.store.create(db, "planner.plan", scope, {**fields, **schedule(fields), "status": "draft"})
            return self._write_result(entity, True, scope, "planner.plan.created")
        entity = self._required("planner.plan", scope, fields["id"], db)
        if action == "apply":
            if entity["fields"]["input_digest"] != fields["input_digest"]:
                raise DomainError("plan_drift")
            changes = {"status": "applied"}
        else:
            changes = {**fields["inputs"], **schedule(fields["inputs"]), "status": "draft"}
        entity, changed = self.store.update(db, "planner.plan", scope, entity["id"], fields["expected_version"], changes)
        return self._write_result(entity, changed, scope, "planner.plan." + action)

    def _review(self, action, fields, scope, db):
        if action == "generate":
            if date.fromisoformat(fields["period_end"]) < date.fromisoformat(fields["period_start"]):
                raise DomainError("invalid_period")
            metrics = {}
            for source, values in fields["sources"].items():
                if any(type(v) not in {int, float} or v < 0 for v in values.values()):
                    raise DomainError("invalid_metric")
                metrics[source] = values
            task = fields["sources"].get("task", {})
            denominator = task.get("planned", 0)
            metrics["task_completion_rate"] = task.get("completed", 0) / denominator if denominator else None
            entity = self.store.create(db, "review", scope, {**fields, "metrics": metrics, "status": "draft"})
            return self._write_result(entity, True, scope, "review.created")
        if action in {"get", "list"}:
            if action == "get":
                return {"entity": self._required("review", scope, fields["id"], db)}, []
            return {"items": self.store.list("review", scope, db), "next_cursor": None}, []
        entity = self._required("review", scope, fields["id"], db)
        if action == "compare":
            other = self._required("review", scope, fields["other_id"], db)
            left, right = entity["fields"]["metrics"], other["fields"]["metrics"]
            return {"left": left, "right": right,
                    "comparable": set(entity["fields"]["sources"]) == set(other["fields"]["sources"])}, []
        if action == "finalize":
            changes = {"status": "finalized"}
        else:
            evidence(fields["evidence_refs"])
            changes = {"interpretation": fields["judgement"], "evidence_refs": fields["evidence_refs"]}
        entity, changed = self.store.update(db, "review", scope, fields["id"], fields["expected_version"], changes)
        return self._write_result(entity, changed, scope, "review." + action)
