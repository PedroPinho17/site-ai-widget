-- Phase 2: company PDF documents and hybrid RAG chunks
CREATE TABLE documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    original_name text NOT NULL,
    storage_name  text NOT NULL,
    size_bytes    bigint NOT NULL CHECK (size_bytes >= 0),
    sha256        text NOT NULL,
    page_count    integer,
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    error         text,
    created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, sha256),
    UNIQUE (id, company_id)
);

CREATE INDEX documents_company_idx ON documents (company_id);
CREATE INDEX documents_company_status_idx ON documents (company_id, status);

CREATE TABLE document_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    document_id uuid NOT NULL,
    page        integer NOT NULL CHECK (page >= 1),
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    content     text NOT NULL,
    embedding   vector(384) NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    FOREIGN KEY (document_id, company_id) REFERENCES documents(id, company_id) ON DELETE CASCADE,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX document_chunks_embedding_hnsw_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX document_chunks_tsv_gin_idx
    ON document_chunks USING gin (tsv);
CREATE INDEX document_chunks_company_document_idx
    ON document_chunks (company_id, document_id);
