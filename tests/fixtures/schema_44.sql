
CREATE TABLE projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL, revision INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
    task_prefix TEXT NOT NULL UNIQUE CHECK(task_prefix GLOB '[A-Z][A-Z][A-Z]')
);
CREATE TABLE milestones (
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL, data TEXT NOT NULL,
    number INTEGER NOT NULL, key TEXT NOT NULL,
    PRIMARY KEY (project_id, id), UNIQUE(project_id, number), UNIQUE(project_id, key)
);
CREATE TABLE tasks (
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
    position INTEGER NOT NULL, data TEXT NOT NULL, number INTEGER NOT NULL,
    key TEXT NOT NULL UNIQUE, PRIMARY KEY (project_id, id), UNIQUE(project_id, number)
);
CREATE TABLE task_revisions (
    project_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id, revision),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE TABLE activity (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('note', 'handoff', 'event')),
    body TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL,
    supersedes TEXT UNIQUE REFERENCES activity(id), task_revision INTEGER, question_id TEXT,
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX activity_scope ON activity(project_id, task_id, sequence);
CREATE TABLE questions (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
    task_id TEXT, status TEXT NOT NULL, data TEXT NOT NULL,
    UNIQUE(project_id, id),
    FOREIGN KEY (project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX question_scope ON questions(project_id, task_id, status);
CREATE TABLE question_revisions (
    project_id TEXT NOT NULL, question_id TEXT NOT NULL,
    revision INTEGER NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY(project_id, question_id, revision),
    FOREIGN KEY (project_id, question_id) REFERENCES questions(project_id, id)
);
PRAGMA user_version = 44;

CREATE TABLE worker_settings (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL
);
CREATE TABLE runs (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
    status TEXT NOT NULL, data TEXT NOT NULL, assignment TEXT NOT NULL,
    FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX runs_project ON runs(project_id, task_id, number);
CREATE UNIQUE INDEX run_owner ON runs(project_id, task_id)
WHERE status IN ('preparing', 'running', 'stopping', 'uncertain');
CREATE TABLE execution_local (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), data TEXT NOT NULL
);

CREATE TABLE integration_settings (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL
);
CREATE TABLE integrations (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX integrations_project ON integrations(project_id, number);

CREATE TABLE result_versions (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    UNIQUE(project_id, task_id, version),
    FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id, id)
);
CREATE INDEX result_work ON result_versions(project_id, status, number);

CREATE VIRTUAL TABLE search_docs USING fts5(
    project_id UNINDEXED, entity UNINDEXED, identity UNINDEXED, revision UNINDEXED,
    kind UNINDEXED, task_id UNINDEXED, created_at UNINDEXED, title, body,
    tokenize='unicode61'
);

CREATE TRIGGER search_projects_insert AFTER INSERT ON projects BEGIN
    INSERT INTO search_docs VALUES (new.id,'project',new.id,new.revision,'project',NULL,
                                   new.updated_at,new.name,new.description);
END;
CREATE TRIGGER search_projects_update AFTER UPDATE ON projects BEGIN
    DELETE FROM search_docs WHERE project_id=new.id AND entity='project';
    INSERT INTO search_docs VALUES (new.id,'project',new.id,new.revision,'project',NULL,
                                   new.updated_at,new.name,new.description);
END;
CREATE TRIGGER search_task_revisions_insert AFTER INSERT ON task_revisions BEGIN INSERT INTO search_docs VALUES (new.project_id,'task',new.task_id,new.revision,json_extract(new.data,'$.task_type'),new.task_id,json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),json_extract(new.data,'$.body')); END;CREATE TRIGGER search_question_revisions_insert AFTER INSERT ON question_revisions BEGIN INSERT INTO search_docs VALUES (new.project_id,'question',new.question_id,new.revision,'question',json_extract(new.data,'$.task_id'),json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.question'),coalesce(json_extract(new.data,'$.context'),'')||char(10)||coalesce(json_extract(new.data,'$.recommendation'),'')||char(10)||coalesce(json_extract(new.data,'$.answer'),'')); END;CREATE TRIGGER search_activity_insert AFTER INSERT ON activity BEGIN INSERT INTO search_docs VALUES (new.project_id,'activity',new.id,new.sequence,new.kind,new.task_id,new.created_at,new.kind,new.body); END;CREATE TRIGGER search_milestones_insert AFTER INSERT ON milestones BEGIN INSERT INTO search_docs VALUES (new.project_id,'milestone',new.id,json_extract(new.data,'$.revision'),'milestone',NULL,json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),json_extract(new.data,'$.body')); END;CREATE TRIGGER search_milestones_update AFTER UPDATE ON milestones BEGIN DELETE FROM search_docs WHERE project_id=new.project_id AND entity='milestone' AND identity=new.id; INSERT INTO search_docs VALUES (new.project_id,'milestone',new.id,json_extract(new.data,'$.revision'),'milestone',NULL,json_extract(new.data,'$.updated_at'),json_extract(new.data,'$.title'),json_extract(new.data,'$.body')); END;CREATE TRIGGER search_result_versions_insert AFTER INSERT ON result_versions BEGIN INSERT INTO search_docs VALUES (new.project_id,'result',new.id,json_extract(new.data,'$.revision'),'result',new.task_id,json_extract(new.data,'$.created_at'),json_extract(new.data,'$.report.summary'),coalesce(json_extract(new.data,'$.report.checks'),'')||char(10)||coalesce(json_extract(new.data,'$.report.limitations'),'')||char(10)||coalesce(json_extract(new.data,'$.feedback'),'')||char(10)||coalesce(json_extract(new.data,'$.problem'),'')); END;CREATE TRIGGER search_result_versions_update AFTER UPDATE ON result_versions BEGIN DELETE FROM search_docs WHERE project_id=new.project_id AND entity='result' AND identity=new.id; INSERT INTO search_docs VALUES (new.project_id,'result',new.id,json_extract(new.data,'$.revision'),'result',new.task_id,json_extract(new.data,'$.created_at'),json_extract(new.data,'$.report.summary'),coalesce(json_extract(new.data,'$.report.checks'),'')||char(10)||coalesce(json_extract(new.data,'$.report.limitations'),'')||char(10)||coalesce(json_extract(new.data,'$.feedback'),'')||char(10)||coalesce(json_extract(new.data,'$.problem'),'')); END;
CREATE TABLE inspection_settings (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), data TEXT NOT NULL
);
CREATE TABLE inspections (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    result_id TEXT REFERENCES result_versions(id),
    data TEXT NOT NULL
);
CREATE INDEX inspections_source ON inspections(project_id, result_id, number);

CREATE TABLE stage_plans (
    project_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY(project_id,task_id,revision),
    FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id)
);

CREATE TABLE task_replies (
    project_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL, data TEXT NOT NULL,
    FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id)
);
CREATE INDEX reply_scope ON task_replies(project_id,task_id,created_at);
CREATE VIEW work_runs AS SELECT * FROM runs WHERE json_extract(data,'$.purpose')='work';

CREATE TABLE run_activity (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), revision INTEGER NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE storage_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision >= 0));
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, app_version TEXT NOT NULL, backup TEXT);
CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, source TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, dismissed_at TEXT, resolved_at TEXT);
CREATE TABLE notification_deliveries (notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE, channel TEXT NOT NULL, claimed_at TEXT NOT NULL, PRIMARY KEY(notification_id, channel));
CREATE TABLE notification_state (name TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE agent_settings (project_id TEXT NOT NULL REFERENCES projects(id), role TEXT NOT NULL CHECK(role IN ('worker','coordinator')), scope TEXT NOT NULL, revision INTEGER NOT NULL, selection TEXT, PRIMARY KEY(project_id,role,scope));
CREATE TABLE agent_permissions (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT, binding TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));
CREATE INDEX permissions_project ON agent_permissions(project_id,task_id,number);
CREATE INDEX permissions_binding ON agent_permissions(binding,status);
CREATE TABLE coordinator_conversations (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), created_at TEXT NOT NULL);
CREATE TABLE coordinator_turns (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), conversation_id TEXT NOT NULL REFERENCES coordinator_conversations(id), status TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX coordinator_projects ON coordinator_conversations(project_id,number);
CREATE INDEX coordinator_history ON coordinator_turns(conversation_id,number);
CREATE UNIQUE INDEX coordinator_active ON coordinator_turns(project_id) WHERE status IN ('starting','running','stopping','uncertain');
CREATE INDEX coordinator_project_messages ON coordinator_turns(project_id,number);
CREATE TABLE attachments (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT, name TEXT NOT NULL, mime TEXT NOT NULL, created_at TEXT NOT NULL, content BLOB NOT NULL, size INTEGER NOT NULL, bound INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id));
CREATE INDEX attachment_project ON attachments(project_id,task_id);
CREATE TABLE coordinator_sessions (project_id TEXT PRIMARY KEY REFERENCES projects(id), harness TEXT NOT NULL, session_id TEXT NOT NULL, cwd TEXT NOT NULL);
INSERT INTO storage_metadata VALUES (1, lower(hex(randomblob(16))), 0);
