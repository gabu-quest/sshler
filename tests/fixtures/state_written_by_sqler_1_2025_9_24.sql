-- A state database of sshler as its own state functions wrote it under sqler 1.2025.9.24.2
-- (the release before sqler 1.2026.11): favourites, sessions, directory visits, a layout,
-- snippets, a progress bar, a diff notebook, an artifact project and a registration.
-- Made by make_state_fixture.py in this folder, then dumped with sqlite3 iterdump().
BEGIN TRANSACTION;
CREATE TABLE artifact_projects (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "artifact_projects" VALUES(1,'{"id":"GEfZRIFMfFrieZhk","name":"My Project","normalized_name":"my project","slug":"my-project","created_at":1795236824.698237,"updated_at":1795236824.698237}');
CREATE TABLE artifact_registrations (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "artifact_registrations" VALUES(1,'{"id":"H9eICau214btWUdcbWVV4A","project_id":"GEfZRIFMfFrieZhk","group_path":"g","source_path":"/srv/a/index.html","mode":"file","entrypoint":null,"title_override":"T","slug":"t","mount_path":"","created_at":1795236824.7028458,"updated_at":1795236824.7028458}');
CREATE TABLE diff_notebooks (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "diff_notebooks" VALUES(1,'{"id":"1BoU8YCXfM4","label":"nb","envelope_json":"{\"v\": 1, \"cells\": []}","cell_count":0,"created_at":1795236824.6940362,"updated_at":1795236824.6940362}');
CREATE TABLE directory_visits (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "directory_visits" VALUES(1,'{"box":"local","path":"/srv/a","visit_count":3,"last_visited":1795236824.671327,"first_visited":1795236824.6624515}');
INSERT INTO "directory_visits" VALUES(2,'{"box":"local","path":"/srv/b","visit_count":1,"last_visited":1795236824.6760352,"first_visited":1795236824.6760352}');
CREATE TABLE favorites (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "favorites" VALUES(1,'{"box":"local","path":"/srv/a","position":0,"created_at":1795236824.631289,"updated_at":1795236824.631289}');
INSERT INTO "favorites" VALUES(2,'{"box":"local","path":"/srv/b","position":1,"created_at":1795236824.631289,"updated_at":1795236824.631289}');
INSERT INTO "favorites" VALUES(3,'{"box":"local","path":"/srv/\u540d\u524d","position":2,"created_at":1795236824.631289,"updated_at":1795236824.631289}');
INSERT INTO "favorites" VALUES(4,'{"box":"web","path":"/var/www","position":0,"created_at":1795236824.644614,"updated_at":1795236824.644614}');
CREATE TABLE layouts (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "layouts" VALUES(1,'{"id":"9cc9b5c4-0025-424c-8c8b-421a923eec99","name":"two","terminals_json":"[{\"box\": \"local\", \"dir\": \"/srv/a\"}, {\"box\": \"web\", \"dir\": \"/var/www\"}]","created_at":1795236824.680542}');
CREATE TABLE progress_bars (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "progress_bars" VALUES(1,'{"name":"job.one","current":3.0,"total":10.0,"color":"blue","label":"Job","status":"running","created_at":1795236824.689424,"updated_at":1795236824.689424,"metadata_json":"{\"a\": 1}","metadata_error":null}');
CREATE TABLE sessions (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "sessions" VALUES(1,'{"id":"1S83R3Lz-qtdLNlO5P1-UQ","box":"local","session_name":"alpha","working_directory":"/srv/a","created_at":1795236824.6489925,"last_accessed_at":1795236824.6489925,"active":true,"window_count":1,"metadata_json":"{\"k\": [1, 2.5, null], \"windows\": [{\"index\": 0, \"name\": \"zsh\", \"cwd\": \"/srv/a\"}], \"last_snapshot_at\": 1795236824.6537158}"}');
INSERT INTO "sessions" VALUES(2,'{"id":"xayB-3DViVejNVOBFEmZVw","box":"web","session_name":"beta","working_directory":"/var/www","created_at":1795236824.6580353,"last_accessed_at":1795236824.6580353,"active":true,"window_count":1,"metadata_json":"{}"}');
CREATE TABLE snippets (
            _id INTEGER PRIMARY KEY AUTOINCREMENT,
            data JSON NOT NULL
        );
INSERT INTO "snippets" VALUES(1,'{"id":"ssn4KqYMWbhrJxHsUzOUtA","box":"__global__","label":"System Info","command":"uname -a","category":"System","sort_order":0,"created_at":1795236824.6144168}');
INSERT INTO "snippets" VALUES(2,'{"id":"GK4qv_PI8nV2v0l7_Oedcw","box":"__global__","label":"Who Am I","command":"whoami && hostname","category":"System","sort_order":1,"created_at":1795236824.6188147}');
INSERT INTO "snippets" VALUES(3,'{"id":"RKu0DA4XEl1l4gvjxzitVg","box":"__global__","label":"Disk Usage","command":"df -h","category":"System","sort_order":2,"created_at":1795236824.6228752}');
INSERT INTO "snippets" VALUES(4,'{"id":"Q-dic7DrFEXFW9F-waRgJA","box":"__global__","label":"Top Processes","command":"ps aux --sort=-%mem | head -20","category":"System","sort_order":3,"created_at":1795236824.6271253}');
INSERT INTO "snippets" VALUES(5,'{"id":"o6rl5EEplfTPcisa0CzD8A","box":"local","label":"list","command":"ls -la","category":"files","sort_order":0,"created_at":1795236824.6850562}');
CREATE INDEX idx_favorites_box ON favorites (json_extract(data, '$.box')) ;
CREATE INDEX idx_favorites_position ON favorites (json_extract(data, '$.position')) ;
CREATE INDEX idx_sessions_box ON sessions (json_extract(data, '$.box')) ;
CREATE INDEX idx_sessions_last_accessed_at ON sessions (json_extract(data, '$.last_accessed_at')) ;
CREATE INDEX idx_sessions_active ON sessions (json_extract(data, '$.active')) ;
CREATE INDEX idx_sessions_session_name ON sessions (json_extract(data, '$.session_name')) ;
CREATE INDEX idx_directory_visits_box ON directory_visits (json_extract(data, '$.box')) ;
CREATE INDEX idx_directory_visits_path ON directory_visits (json_extract(data, '$.path')) ;
CREATE INDEX idx_directory_visits_last_visited ON directory_visits (json_extract(data, '$.last_visited')) ;
CREATE INDEX idx_snippets_box ON snippets (json_extract(data, '$.box')) ;
CREATE INDEX idx_snippets_sort_order ON snippets (json_extract(data, '$.sort_order')) ;
CREATE INDEX idx_layouts_name ON layouts (json_extract(data, '$.name')) ;
CREATE INDEX idx_layouts_created_at ON layouts (json_extract(data, '$.created_at')) ;
CREATE INDEX idx_progress_bars_name ON progress_bars (json_extract(data, '$.name')) ;
CREATE INDEX idx_progress_bars_updated_at ON progress_bars (json_extract(data, '$.updated_at')) ;
CREATE INDEX idx_diff_notebooks_id ON diff_notebooks (json_extract(data, '$.id')) ;
CREATE INDEX idx_diff_notebooks_created_at ON diff_notebooks (json_extract(data, '$.created_at')) ;
CREATE INDEX idx_artifact_projects_id ON artifact_projects (json_extract(data, '$.id')) ;
CREATE INDEX idx_artifact_projects_normalized_name ON artifact_projects (json_extract(data, '$.normalized_name')) ;
CREATE INDEX idx_artifact_projects_slug ON artifact_projects (json_extract(data, '$.slug')) ;
CREATE INDEX idx_artifact_registrations_id ON artifact_registrations (json_extract(data, '$.id')) ;
CREATE INDEX idx_artifact_registrations_project_id ON artifact_registrations (json_extract(data, '$.project_id')) ;
CREATE INDEX idx_artifact_registrations_source_path ON artifact_registrations (json_extract(data, '$.source_path')) ;
CREATE INDEX idx_artifact_registrations_slug ON artifact_registrations (json_extract(data, '$.slug')) ;
CREATE INDEX idx_artifact_registrations_mount_path ON artifact_registrations (json_extract(data, '$.mount_path')) ;
CREATE INDEX idx_sessions_box_name
        ON sessions(json_extract(data, '$.box'), json_extract(data, '$.session_name'))
        ;
CREATE INDEX idx_sessions_active_accessed
        ON sessions(json_extract(data, '$.active'), json_extract(data, '$.last_accessed_at'))
        ;
CREATE INDEX idx_favorites_box_path
        ON favorites(json_extract(data, '$.box'), json_extract(data, '$.path'))
        ;
CREATE INDEX idx_directory_visits_box_path
        ON directory_visits(json_extract(data, '$.box'), json_extract(data, '$.path'))
        ;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('snippets',5);
INSERT INTO "sqlite_sequence" VALUES('favorites',4);
INSERT INTO "sqlite_sequence" VALUES('sessions',2);
INSERT INTO "sqlite_sequence" VALUES('directory_visits',2);
INSERT INTO "sqlite_sequence" VALUES('layouts',1);
INSERT INTO "sqlite_sequence" VALUES('progress_bars',1);
INSERT INTO "sqlite_sequence" VALUES('diff_notebooks',1);
INSERT INTO "sqlite_sequence" VALUES('artifact_projects',1);
INSERT INTO "sqlite_sequence" VALUES('artifact_registrations',1);
COMMIT;
