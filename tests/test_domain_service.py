import pytest
from business_runtime.store import Store, DomainError
from business_runtime.domains import DomainService

def service(tmp_path,slug): return DomainService(slug,Store(tmp_path/(slug+".sqlite"),"yushuos."+slug))
def apply(s,cap,fields):
 with s.store.write() as db:return s.operate(cap,fields,"personal",db)

def test_capture_requires_completion_evidence(tmp_path):
 s=service(tmp_path,"capture")
 e=apply(s,"capture.create",{"content":"remember this","source":"manual"})[0]["entity"]
 with pytest.raises(DomainError,match="evidence_required"):
  apply(s,"capture.mark_processed",{"id":e["id"],"expected_version":1})
 done=apply(s,"capture.mark_processed",{"id":e["id"],"expected_version":1,"ignored_reason":"duplicate"})[0]["entity"]
 assert done["fields"]["status"]=="processed"

def test_habit_checkin_unique_date_and_undo(tmp_path):
 s=service(tmp_path,"habit")
 e=apply(s,"habit.create",{"name":"walk","timezone":"Asia/Shanghai"})[0]["entity"]
 args={"habit_id":e["id"],"date":"2026-10-06"}
 a=apply(s,"habit.checkin",args)[0]
 b=apply(s,"habit.checkin",args)[0]
 assert a["changed"] and not b["changed"]
 assert len(s.store.list("habit.checkin","personal"))==1
 assert apply(s,"habit.undo_checkin",args)[0]["changed"]

def test_finance_decimal_currency_and_duplicate(tmp_path):
 s=service(tmp_path,"finance")
 f={"month":"2026-10","currency":"CNY","source_id":"bill1","transactions":[{"amount":"0.10","direction":"expense"},{"amount":"0.20","direction":"expense"}]}
 e=apply(s,"finance.statement.create",f)[0]["entity"]
 assert e["fields"]["expense"]=="0.30"
 assert not apply(s,"finance.statement.create",f)[0]["changed"]
 with pytest.raises(DomainError,match="invalid_amount"):
  apply(s,"finance.statement.create",{**f,"source_id":"bad","transactions":[{"amount":"NaN","direction":"expense"}]})

def test_deepwork_pause_resume_finish_uses_timestamps(tmp_path):
 s=service(tmp_path,"deepwork")
 e=apply(s,"deepwork.start",{"task_ref":{"provider":"yushuos.task","kind":"task","id":"task1","store_id":"personal"},"at":"2026-10-06T00:00:00Z"})[0]["entity"]
 e=apply(s,"deepwork.pause",{"id":e["id"],"expected_version":1,"at":"2026-10-06T00:10:00Z"})[0]["entity"]
 e=apply(s,"deepwork.resume",{"id":e["id"],"expected_version":2,"at":"2026-10-06T00:20:00Z"})[0]["entity"]
 e=apply(s,"deepwork.finish",{"id":e["id"],"expected_version":3,"at":"2026-10-06T00:30:00Z"})[0]["entity"]
 assert e["fields"]["active_seconds"]==1200
 with pytest.raises(DomainError,match="invalid_transition"):
  apply(s,"deepwork.resume",{"id":e["id"],"expected_version":4})

def test_personal_model_requires_evidence_and_host_provided_judgement(tmp_path):
 s=service(tmp_path,"personal_model")
 e=apply(s,"personal_model.hypothesis.create",{"statement":"morning focus","confidence":0.5,"evidence_refs":[]})[0]["entity"]
 with pytest.raises(DomainError,match="evidence_required"):
  apply(s,"personal_model.evaluate",{"id":e["id"],"expected_version":1,"confidence":0.9,"reason":"guess"})

def test_review_metrics_handle_missing_sources(tmp_path):
 s=service(tmp_path,"review")
 e=apply(s,"review.generate",{"period_start":"2026-10-01","period_end":"2026-10-07","sources":{"task":{"planned":0,"completed":0}},"missing_sources":["habit"]})[0]["entity"]
 assert e["fields"]["metrics"]["task_completion_rate"] is None
 assert e["fields"]["missing_sources"]==["habit"]
