BEGIN TRANSACTION;
CREATE TABLE schema_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                schema_version INTEGER NOT NULL CHECK(schema_version = 1),
                store_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
INSERT INTO "schema_meta" VALUES(1,1,'store-a','2026-10-06T01:16:17.774577Z');
CREATE TABLE task_request_commits (
                plugin_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64),
                fingerprint_scheme TEXT NOT NULL CHECK(fingerprint_scheme = 'jcs-operation-v1'),
                operation_name TEXT NOT NULL CHECK(operation_name IN (
                    'task.create','task.update','task.complete','task.reopen','task.delete'
                )),
                core_project_ref TEXT NOT NULL CHECK(length(core_project_ref) <= 200),
                provider_version TEXT NOT NULL,
                provider_digest TEXT NOT NULL CHECK(length(provider_digest) = 64),
                task_id TEXT NOT NULL,
                operation_status TEXT NOT NULL CHECK(operation_status = 'committed'),
                changed INTEGER NOT NULL CHECK(changed IN (0, 1)),
                error_code TEXT,
                result_body TEXT,
                committed_at TEXT NOT NULL,
                result_body_expires_at TEXT NOT NULL,
                event_intents TEXT NOT NULL,
                event_recovery_state TEXT NOT NULL CHECK(event_recovery_state IN ('none','pending','ledger_recorded')),
                PRIMARY KEY(plugin_id, store_id, request_id),
                FOREIGN KEY(store_id, task_id) REFERENCES tasks(store_id, id),
                CHECK((event_recovery_state = 'none' AND event_intents = '[]') OR
                      (event_recovery_state IN ('pending','ledger_recorded') AND event_intents != '[]'))
            );
INSERT INTO "task_request_commits" VALUES('yushuos.task','store-a','req-1','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','jcs-operation-v1','task.create','core-project','0.1.0','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','tsk_11111111111111111111111111111111','committed',1,NULL,'{"changed":true,"operation_status":"committed","result_state":"available","task":{"completed_at":null,"created_at":"2026-10-06T01:00:00.000000Z","deleted_at":null,"due_at":null,"estimate_minutes":null,"id":"tsk_11111111111111111111111111111111","notes":null,"priority":"normal","project_ref":null,"source_ref":null,"status":"open","tags":["a","b"],"title":"private original","updated_at":"2026-10-06T01:00:00.000000Z","version":1}}','2026-10-06T01:00:00.000000Z','2027-04-04T01:00:00.000000Z','[{"resource_refs":{"task_id":"tsk_11111111111111111111111111111111"},"type":"task.created"}]','pending');
CREATE TABLE task_tags (
                store_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                tag TEXT NOT NULL CHECK(length(trim(tag)) BETWEEN 1 AND 64 AND tag = trim(tag)),
                position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 31),
                PRIMARY KEY(store_id, task_id, tag),
                UNIQUE(store_id, task_id, position),
                FOREIGN KEY(store_id, task_id) REFERENCES tasks(store_id, id) ON DELETE CASCADE
            );
INSERT INTO "task_tags" VALUES('store-a','tsk_11111111111111111111111111111111','a',0);
INSERT INTO "task_tags" VALUES('store-a','tsk_11111111111111111111111111111111','b',1);
CREATE TABLE tasks (
                store_id TEXT NOT NULL,
                id TEXT NOT NULL,
                title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 500),
                notes TEXT CHECK(notes IS NULL OR length(notes) <= 20000),
                status TEXT NOT NULL CHECK(status IN ('open', 'completed', 'deleted')),
                priority TEXT NOT NULL CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
                project_ref TEXT CHECK(project_ref IS NULL OR (length(trim(project_ref)) BETWEEN 1 AND 200 AND project_ref = trim(project_ref))),
                due_at TEXT,
                estimate_minutes INTEGER CHECK(estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND 10080),
                source_ref TEXT CHECK(source_ref IS NULL OR (length(trim(source_ref)) BETWEEN 1 AND 200 AND source_ref = trim(source_ref))),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                deleted_at TEXT,
                version INTEGER NOT NULL CHECK(version > 0),
                PRIMARY KEY(store_id, id),
                UNIQUE(id),
                CHECK(length(id) = 36 AND substr(id, 1, 4) = 'tsk_'),
                CHECK(substr(id, 5) NOT GLOB '*[^0-9a-f]*'),
                CHECK(length(created_at) = 27 AND substr(created_at, 27, 1) = 'Z'),
                CHECK(length(updated_at) = 27 AND substr(updated_at, 27, 1) = 'Z'),
                CHECK(due_at IS NULL OR (length(due_at) = 27 AND substr(due_at, 27, 1) = 'Z')),
                CHECK(completed_at IS NULL OR (length(completed_at) = 27 AND substr(completed_at, 27, 1) = 'Z')),
                CHECK(deleted_at IS NULL OR (length(deleted_at) = 27 AND substr(deleted_at, 27, 1) = 'Z')),
                CHECK(status != 'completed' OR completed_at IS NOT NULL),
                CHECK(status != 'open' OR completed_at IS NULL),
                CHECK(status != 'deleted' OR deleted_at IS NOT NULL),
                CHECK(status = 'deleted' OR deleted_at IS NULL)
            );
INSERT INTO "tasks" VALUES('store-a','tsk_11111111111111111111111111111111','private original',NULL,'open','normal',NULL,NULL,NULL,NULL,'2026-10-06T01:00:00.000000Z','2026-10-06T01:00:00.000000Z',NULL,NULL,1);
CREATE INDEX tasks_created_order ON tasks(store_id, created_at DESC, id DESC);
CREATE INDEX tasks_status_order ON tasks(store_id, status, created_at DESC, id DESC);
CREATE INDEX tasks_project_order ON tasks(store_id, project_ref, created_at DESC, id DESC);
CREATE INDEX tasks_priority_order ON tasks(store_id, priority, created_at DESC, id DESC);
CREATE INDEX tasks_due_order ON tasks(store_id, due_at, created_at DESC, id DESC);
CREATE INDEX tasks_updated_order ON tasks(store_id, updated_at, created_at DESC, id DESC);
CREATE INDEX task_tags_by_value ON task_tags(store_id, tag, task_id);
CREATE INDEX task_commits_expiry ON task_request_commits(store_id, result_body_expires_at) WHERE result_body IS NOT NULL;
COMMIT;
PRAGMA user_version=1;
