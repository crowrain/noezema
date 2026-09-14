"""0004: memory model — claims, evidence, assessments, independence (T3.1, §8, §14).

Adds the durable knowledge model:

- claims + claim_dependencies (scaffold; the DAG and cascade are M4)
- evidence with the per-kind identity constraints of §14.3
- claim_revisions (history of stable-field changes)
- claim_assessments + claim_assessment_heads (the lifecycle source of
  truth, §8.2/§14.1) + assessment_evidence (role enum)
- operator_attestations (never raise the grade, §3.7)
- sources + source_independence_snapshots/members (T3.5)
- environment_manifests (scaffold; full fields land with M4)
- checkpoints + backup_manifests (§15.3)
"""

from __future__ import annotations

from alembic import op

revision = "0004_memory"
down_revision = "0003_commit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── claims (§8.2, §14) ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE claims (
            id                    uuid PRIMARY KEY,
            statement             text NOT NULL CHECK (length(statement) <= 2000),
            claim_type            text NOT NULL
                                  CHECK (claim_type IN ('local_observation','computed_result',
                                      'formal_theorem','empirical_conjecture','procedural',
                                      'external_fact','temporal_fact','self_model')),
            freshness_status      text NOT NULL DEFAULT 'unknown'
                                  CHECK (freshness_status IN ('fresh','due','stale','unknown')),
            valid_from            timestamptz,
            valid_to              timestamptz,
            as_of                 timestamptz,
            observed_at           timestamptz,
            reverify_after        timestamptz,
            dependency_fingerprint text,
            topic                 text,
            created_in_session    uuid REFERENCES sessions(id) ON DELETE SET NULL,
            created_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_claims_type ON claims (claim_type)")
    op.execute("CREATE INDEX ix_claims_reverify ON claims (reverify_after)")

    # dependency graph scaffold (direction: from depends on to; §14.1)
    op.execute(
        """
        CREATE TABLE claim_dependencies (
            id                uuid PRIMARY KEY,
            from_claim_id     uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            to_claim_id       uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            kind              text NOT NULL DEFAULT 'evidential'
                              CHECK (kind IN ('evidential','investigative')),
            created_in_session uuid REFERENCES sessions(id) ON DELETE SET NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (from_claim_id, to_claim_id, kind),
            CHECK (from_claim_id <> to_claim_id)
        )
        """
    )

    # ── evidence (§14.3) ──────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE evidence (
            id                      uuid PRIMARY KEY,
            claim_id                uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            relation                text NOT NULL CHECK (relation IN ('supports','counters')),
            evidence_kind           text NOT NULL
                                    CHECK (evidence_kind IN ('source_assertion','quote_integrity',
                                        'experiment_run','computation','formal_check','local_observation')),
            identity_hash           text NOT NULL,
            scope                   jsonb NOT NULL DEFAULT '{}'::jsonb,
            source_id               uuid,
            chunk_id                text,
            observation_artifact_id uuid REFERENCES artifacts(id) ON DELETE SET NULL,
            environment_manifest_id uuid,
            created_in_session      uuid REFERENCES sessions(id) ON DELETE SET NULL,
            created_at              timestamptz NOT NULL DEFAULT now(),
            UNIQUE (claim_id, evidence_kind, identity_hash),
            CHECK (
                (evidence_kind IN ('source_assertion','quote_integrity')
                    AND source_id IS NOT NULL AND chunk_id IS NOT NULL)
                OR
                (evidence_kind IN ('experiment_run','local_observation')
                    AND observation_artifact_id IS NOT NULL AND environment_manifest_id IS NOT NULL)
                OR
                (evidence_kind IN ('computation','formal_check')
                    AND observation_artifact_id IS NOT NULL)
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_evidence_claim ON evidence (claim_id)")
    op.execute("CREATE INDEX ix_evidence_identity ON evidence (evidence_kind, identity_hash)")

    # ── claim revisions (§8.2 history) ────────────────────────────────────
    op.execute(
        """
        CREATE TABLE claim_revisions (
            id                       uuid PRIMARY KEY,
            claim_id                 uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            session_id               uuid REFERENCES sessions(id) ON DELETE SET NULL,
            previous_value           jsonb NOT NULL,
            new_value                jsonb NOT NULL,
            changed_at               timestamptz NOT NULL DEFAULT now(),
            reason_audit_event_id    uuid
        )
        """
    )
    op.execute("CREATE INDEX ix_claim_revisions_claim ON claim_revisions (claim_id)")

    # ── assessments (§3.7, §8.2, §14.1) ───────────────────────────────────
    op.execute(
        """
        CREATE TABLE claim_assessments (
            id                             uuid PRIMARY KEY,
            claim_id                       uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            effective_grade                text NOT NULL
                                           CHECK (effective_grade IN ('E0','E1','E2','E3','E4')),
            epistemic_status               text NOT NULL
                                           CHECK (epistemic_status IN ('hypothesis','supported',
                                               'disputed','refuted','deferred')),
            rules_version                  text NOT NULL,
            rules_hash                     text NOT NULL,
            source_independence_snapshot_id uuid,
            environment_independence_snapshot_id uuid,
            evidence_set_hash              text NOT NULL,
            assessed_scope                 jsonb NOT NULL DEFAULT '{}'::jsonb,
            confidence                     double precision NOT NULL
                                           CHECK (confidence >= 0 AND confidence <= 1),
            valid                          boolean NOT NULL DEFAULT true,
            invalidation_reason            text,
            created_in_session             uuid REFERENCES sessions(id) ON DELETE SET NULL,
            created_at                     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_claim_assessments_claim ON claim_assessments (claim_id)")

    op.execute(
        """
        CREATE TABLE claim_assessment_heads (
            claim_id                uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            config_snapshot_id      uuid NOT NULL REFERENCES config_snapshots(id) ON DELETE CASCADE,
            assessment_state        text NOT NULL
                                    CHECK (assessment_state IN ('current','pending','invalid')),
            current_assessment_id   uuid REFERENCES claim_assessments(id) ON DELETE SET NULL,
            epistemic_status        text
                                    CHECK (epistemic_status IS NULL
                                         OR epistemic_status IN ('hypothesis','supported',
                                             'disputed','refuted','deferred')),
            prepared_by             text NOT NULL
                                    CHECK (prepared_by IN ('session','rules_activation','reassessment_worker')),
            updated_at              timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (claim_id, config_snapshot_id),
            CHECK (
                (assessment_state = 'current'
                    AND current_assessment_id IS NOT NULL
                    AND epistemic_status IS NOT NULL)
                OR
                (assessment_state IN ('pending','invalid')
                    AND current_assessment_id IS NULL
                    AND epistemic_status IS NULL)
            )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE assessment_evidence (
            assessment_id uuid NOT NULL REFERENCES claim_assessments(id) ON DELETE CASCADE,
            evidence_id   uuid NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
            role          text NOT NULL
                          CHECK (role IN ('support','counter','scope_witness','context')),
            PRIMARY KEY (assessment_id, evidence_id, role)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE operator_attestations (
            id                        uuid PRIMARY KEY,
            actor_id                  text NOT NULL,
            claim_id                  uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            body                      text NOT NULL,
            supporting_artifact_id    uuid REFERENCES artifacts(id) ON DELETE SET NULL,
            created_at                timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ── sources + independence (§8.5, T3.5) ───────────────────────────────
    op.execute(
        """
        CREATE TABLE sources (
            id               uuid PRIMARY KEY,
            source_type      text NOT NULL
                            CHECK (source_type IN ('local_file','local_corpus','session_artifact',
                                'external_url','operator_input')),
            canonical_uri    text,
            retrieved_at     timestamptz,
            content_hash     text,
            metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
            parent_source_id uuid REFERENCES sources(id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_sources_uri ON sources (canonical_uri)")

    op.execute(
        """
        CREATE TABLE source_independence_snapshots (
            id                     uuid PRIMARY KEY,
            algorithm_version      text NOT NULL,
            thresholds             jsonb NOT NULL,
            psl_fingerprint        text NOT NULL,
            uri_normalizer_version text NOT NULL,
            created_at             timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE source_independence_members (
            snapshot_id uuid NOT NULL REFERENCES source_independence_snapshots(id) ON DELETE CASCADE,
            source_id   uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            group_id    text NOT NULL,
            basis       text NOT NULL,
            PRIMARY KEY (snapshot_id, source_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_indep_members_group ON source_independence_members (snapshot_id, group_id)")

    # ── environment manifests (scaffold; M4 fills the semantics) ─────────
    op.execute(
        """
        CREATE TABLE environment_manifests (
            id                   uuid PRIMARY KEY,
            manifest_artifact_id uuid REFERENCES artifacts(id) ON DELETE SET NULL,
            protocol_hash        text,
            implementation_hash  text,
            code_lineage         text,
            dataset_hash         text,
            dataset_lineage      text,
            toolchain_hash       text,
            dependency_hash      text,
            runtime_hash         text,
            hardware_hash        text,
            seed                 bigint,
            data_order_hash      text,
            normalizer_version   text NOT NULL DEFAULT 'env-v1',
            created_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ── checkpoints + backups (§15.3) ─────────────────────────────────────
    op.execute(
        """
        CREATE TABLE checkpoints (
            id                       uuid PRIMARY KEY,
            session_id               uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            workspace_manifest_id    uuid REFERENCES workspace_manifests(id) ON DELETE SET NULL,
            database_commit_id       text,
            knowledge_revision       bigint NOT NULL,
            dependency_graph_revision bigint NOT NULL,
            created_at               timestamptz NOT NULL DEFAULT now(),
            UNIQUE (session_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE backup_manifests (
            id                           uuid PRIMARY KEY,
            database_recovery_point      text NOT NULL,
            artifact_inventory_hash      text NOT NULL,
            artifact_inventory_artifact_id uuid REFERENCES artifacts(id) ON DELETE SET NULL,
            retention_until              timestamptz,
            verified_at                  timestamptz,
            created_at                   timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:  # pragma: no cover
    for table in (
        "backup_manifests",
        "checkpoints",
        "environment_manifests",
        "source_independence_members",
        "source_independence_snapshots",
        "sources",
        "operator_attestations",
        "assessment_evidence",
        "claim_assessment_heads",
        "claim_assessments",
        "claim_revisions",
        "evidence",
        "claim_dependencies",
        "claims",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
