-- Keep the extracted text of each official document so course-catalogue import can be re-run/validated without re-downloading.
ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS full_text TEXT;
