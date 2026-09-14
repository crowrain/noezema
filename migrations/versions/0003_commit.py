"""0003: artifact store, staging, commit boundary schema (T2.11-T2.18, §5.2.2, §5.11, §14.2).

Adds the K3 (artifacts/staging) and K4 (commit boundary) tables referenced
by 0002 as deferred FKs, plus the canonical domain_revisions seed. The
fencing predicate of the final transaction reads these rows; the reconciler
(PR #15) is the only other writer.
"""

from __future__ import annotations

from alembic import op

revision = "0003_commit"
down_revision = "0002_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── artifact store (§5.11, ADR-0002) ─────────────────────────────────
    op.execute(
        """
        CREATE TABLE artifacts (
            id          uuid PRIMARY KEY,
            sha256      text NOT NULL UNIQUE,
            size        bigint NOT NULL CHECK (size >= 0),
            mime        text,
            origin      text NOT NULL DEFAULT 'session_workspace',
            trust_class text NOT NULL DEFAULT 'untrusted'
                        CHECK (trust_class IN ('local_trusted','session_workspace','untrusted','external')),
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE artifact_chunks (
            id                 uuid PRIMARY KEY,
            artifact_id        uuid NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            chunk_id           text NOT NULL,
            byte_range         text,
            origin_kind        text NOT NULL,
            source_uri         text,
            obtained_at        timestamptz,
            content_hash       text NOT NULL,
            transform_chain    jsonb NOT NULL DEFAULT '[]'::jsonb,
            parser_fingerprint text,
            trust_class        text NOT NULL DEFAULT 'untrusted'
                               CHECK (trust_class IN ('local_trusted','session_workspace','untrusted','external')),
            usage_constraints  jsonb NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (artifact_id, chunk_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_artifact_chunks_artifact ON artifact_chunks (artifact_id)")

    # ── workspace freeze (T2.12) ─────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE workspace_manifests (
            id          uuid PRIMARY KEY,
            session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE SET NULL,
            root_sha256 text NOT NULL,
            entry_count integer NOT NULL CHECK (entry_count >= 0),
            total_size  bigint NOT NULL CHECK (total_size >= 0),
            frozen_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_workspace_manifests_session ON workspace_manifests (session_id)")

    op.execute(
        """
        CREATE TABLE workspace_entries (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            manifest_id uuid NOT NULL REFERENCES workspace_manifests(id) ON DELETE CASCADE,
            path        text NOT NULL,
            size        bigint NOT NULL CHECK (size >= 0),
            sha256      text NOT NULL,
            UNIQUE (manifest_id, path)
        )
        """
    )

    # ── session staging (§5.2.2, T2.13) ──────────────────────────────────
    op.execute(
        """
        CREATE TABLE session_staging (
            id             uuid PRIMARY KEY,
            session_id     uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            op             text NOT NULL
                           CHECK (op IN ('claim','evidence','question','identity')),
            payload        jsonb NOT NULL,
            payload_hash   text NOT NULL,
            schema_version integer NOT NULL DEFAULT 1,
            state          text NOT NULL DEFAULT 'recorded'
                           CHECK (state IN ('recorded','validated','rejected','applied')),
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_session_staging_session ON session_staging (session_id, state)")

    # ── commit boundary (§5.2.2, T2.15-T2.18) ────────────────────────────
    op.execute(
        """
        CREATE TABLE domain_revisions (
            scope      text PRIMARY KEY CHECK (scope IN ('knowledge','dependency_graph')),
            revision   bigint NOT NULL CHECK (revision >= 0),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "INSERT INTO domain_revisions (scope, revision) VALUES ('knowledge', 0), ('dependency_graph', 0)"
    )

    op.execute(
        """
        CREATE TABLE commit_attempts (
            id                              uuid PRIMARY KEY,
            session_id                      uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            status                          text NOT NULL DEFAULT 'prepared'
                                           CHECK (status IN ('prepared','reconciling','committed','aborted')),
            staging_hash                    text NOT NULL,
            workspace_manifest_id           uuid REFERENCES workspace_manifests(id),
            base_knowledge_revision         bigint NOT NULL,
            base_dependency_graph_revision  bigint NOT NULL,
            prepared_at                     timestamptz NOT NULL DEFAULT now(),
            finished_at                     timestamptz
        )
        """
    )
    # §14.2: at most one UNRESOLVED attempt per session
    op.execute(
        "CREATE UNIQUE INDEX uq_commit_attempts_unresolved "
        "ON commit_attempts (session_id) WHERE status IN ('prepared','reconciling')"
    )
    op.execute("CREATE INDEX ix_commit_attempts_session ON commit_attempts (session_id)")

    op.execute(
        """
        CREATE TABLE knowledge_write_gate (
            id                integer PRIMARY KEY CHECK (id = 1),
            owner             text,
            intent_kind       text,
            acquired_at       timestamptz,
            intent_expires_at timestamptz
        )
        """
    )
    op.execute("INSERT INTO knowledge_write_gate (id) VALUES (1)")

    # ── deferred FKs from 0002 ───────────────────────────────────────────
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_commit_attempt "
        "FOREIGN KEY (commit_attempt_id) REFERENCES commit_attempts(id)"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_base_manifest "
        "FOREIGN KEY (base_workspace_manifest_id) REFERENCES workspace_manifests(id)"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_committed_manifest "
        "FOREIGN KEY (committed_workspace_manifest_id) REFERENCES workspace_manifests(id)"
    )
    op.execute(
        "ALTER TABLE model_runs ADD CONSTRAINT fk_model_runs_context_artifact "
        "FOREIGN KEY (context_manifest_artifact_id) REFERENCES artifacts(id)"
    )
    op.execute(
        "ALTER TABLE model_runs ADD CONSTRAINT fk_model_runs_raw_artifact "
        "FOREIGN KEY (raw_response_artifact_id) REFERENCES artifacts(id)"
    )
    op.execute(
        "ALTER TABLE actions ADD CONSTRAINT fk_actions_result_artifact "
        "FOREIGN KEY (result_artifact_id) REFERENCES artifacts(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE actions DROP CONSTRAINT IF EXISTS fk_actions_result_artifact")
    op.execute("ALTER TABLE model_runs DROP CONSTRAINT IF EXISTS fk_model_runs_raw_artifact")
    op.execute("ALTER TABLE model_runs DROP CONSTRAINT IF EXISTS fk_model_runs_context_artifact")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_committed_manifest")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_base_manifest")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_commit_attempt")
    for table in (
        "knowledge_write_gate",
        "commit_attempts",
        "domain_revisions",
        "session_staging",
        "workspace_entries",
        "workspace_manifests",
        "artifact_chunks",
        "artifacts",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
