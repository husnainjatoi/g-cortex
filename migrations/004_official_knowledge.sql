-- Official GIKI knowledge base (RAG) and course-catalogue provenance. Additive; existing courses and policies are untouched.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id        SERIAL PRIMARY KEY,
    url              TEXT NOT NULL UNIQUE,
    title            TEXT,
    doc_type         VARCHAR(10) NOT NULL CHECK (doc_type IN ('page','pdf')),
    content_hash     CHAR(64),
    etag             TEXT,
    last_modified    TEXT,
    http_status      INT,
    byte_size        BIGINT,
    page_count       INT,
    status           VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active','failed','removed')),
    error            TEXT,
    first_fetched_at TIMESTAMP NOT NULL DEFAULT now(),
    fetched_at       TIMESTAMP,
    last_checked_at  TIMESTAMP
);
-- Hard guarantee that nothing outside the official domain can ever be stored as a source.
ALTER TABLE knowledge_sources DROP CONSTRAINT IF EXISTS official_domain_only;
ALTER TABLE knowledge_sources ADD CONSTRAINT official_domain_only
    CHECK (url ~* '^https?://([a-z0-9-]+\.)*giki\.edu\.pk(/|$)');

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id     BIGSERIAL PRIMARY KEY,
    source_id    INT NOT NULL REFERENCES knowledge_sources(source_id) ON DELETE CASCADE,
    chunk_index  INT NOT NULL,
    section      TEXT,
    page_from    INT,
    content      TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    embedding    vector(1536),
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(section,'') || ' ' || content)) STORED,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (content_hash)
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON knowledge_chunks(source_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON knowledge_chunks USING gin(tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS knowledge_runs (
    run_id      SERIAL PRIMARY KEY,
    started_at  TIMESTAMP NOT NULL DEFAULT now(),
    finished_at TIMESTAMP,
    started_by  VARCHAR(10),
    status      VARCHAR(10) NOT NULL DEFAULT 'running' CHECK (status IN ('running','done','failed')),
    summary     JSONB
);

-- Course catalogue provenance.
ALTER TABLE courses ADD COLUMN IF NOT EXISTS catalog_only BOOLEAN NOT NULL DEFAULT FALSE;   -- in the official catalogue, not scheduled/open for registration
ALTER TABLE courses ADD COLUMN IF NOT EXISTS department VARCHAR(120);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS lecture_hours INT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS lab_hours INT;

CREATE TABLE IF NOT EXISTS course_sources (
    course_code       VARCHAR(10) PRIMARY KEY REFERENCES courses(course_code) ON UPDATE CASCADE ON DELETE CASCADE,
    source_id         INT REFERENCES knowledge_sources(source_id),
    source_url        TEXT NOT NULL,
    source_title      TEXT,
    source_section    TEXT,
    source_page       INT,
    official_title    TEXT NOT NULL,
    official_credits  INT NOT NULL,
    official_prereq_text TEXT,
    prereq_status     VARCHAR(10) NOT NULL CHECK (prereq_status IN ('none','parsed','ambiguous','missing')),
    retrieved_at      TIMESTAMP NOT NULL DEFAULT now(),
    last_checked_at   TIMESTAMP NOT NULL DEFAULT now(),
    outcome           VARCHAR(12) NOT NULL CHECK (outcome IN ('imported','matched','conflict'))
);

CREATE TABLE IF NOT EXISTS course_import_flags (
    flag_id     SERIAL PRIMARY KEY,
    course_code VARCHAR(10) NOT NULL,
    kind        VARCHAR(30) NOT NULL,
    detail      JSONB NOT NULL,
    source_url  TEXT,
    status      VARCHAR(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open','applied','dismissed')),
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    resolved_by VARCHAR(10) REFERENCES users(user_id),
    resolved_at TIMESTAMP,
    fingerprint CHAR(32) NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_import_flag ON course_import_flags(fingerprint);

-- The pre-existing, team-authored policy texts are NOT official GIKI sources; keep them but mark them so Ask GIKI never presents them as verified.
ALTER TABLE university_policies ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE;
COMMENT ON COLUMN university_policies.verified IS 'FALSE = authored by the project team, not an official GIKI document; excluded from Ask GIKI answers.';
