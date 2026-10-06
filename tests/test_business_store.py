import pytest
from business_runtime.store import Store, DomainError

def identity(request_id="one", fingerprint="a"*64):
    return {"request_id":request_id,"fingerprint":fingerprint,"provider_digest":"b"*64,"version":"0.1.0","project_ref":"test","intent":"command"}

def test_read_does_not_create_database(tmp_path):
    p=tmp_path/"store.sqlite"
    store=Store(p,"yushuos.habit")
    assert store.list("habit","personal") == []
    assert not p.exists()

def test_private_business_change_and_proof_commit_together(tmp_path):
    store=Store(tmp_path/"store.sqlite","yushuos.habit")
    def mutate(db):
        entity=store.create(db,"habit","personal",{"name":"read"})
        return {"entity":entity,"changed":True},[{"type":"habit.created","resource":{"id":entity["id"]}}]
    proof=store.commit("personal",identity(),mutate)
    assert proof["data"]["entity"]["version"]==1
    assert store.lookup("personal","one")==proof
    assert store.commit("personal",identity(),lambda _:pytest.fail("duplicate mutation"))==proof
    with pytest.raises(DomainError, match="request_conflict"):
        store.commit("personal",identity(fingerprint="c"*64),mutate)

def test_failure_rolls_back_business_and_proof(tmp_path):
    store=Store(tmp_path/"store.sqlite","yushuos.habit")
    def mutate(db):
        store.create(db,"habit","personal",{"name":"read"})
        raise DomainError("habit.invalid")
    with pytest.raises(DomainError):store.commit("personal",identity(),mutate)
    assert store.list("habit","personal")==[]
    assert store.lookup("personal","one") is None

def test_versions_noop_and_store_isolation(tmp_path):
    store=Store(tmp_path/"store.sqlite","yushuos.habit")
    proof=store.commit("A",identity(),lambda db:({"entity":store.create(db,"habit","A",{"name":"read"}),"changed":True},[]))
    e=proof["data"]["entity"]
    with store.write() as db:
        same,changed=store.update(db,"habit","A",e["id"],1,{"name":"read"})
        assert not changed and same["version"]==1
        with pytest.raises(DomainError,match="version_conflict"):
            store.update(db,"habit","A",e["id"],2,{"name":"other"})
    assert store.list("habit","B")==[]
