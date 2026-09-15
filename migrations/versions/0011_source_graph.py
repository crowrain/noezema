"""0011: the full source graph (T4.7, §11.3, §14).

Adds the missing §14 tables:

- ``source_dependency_edges`` — a source points at another source
  (link to a primary source, derived content, republish): the merge
  basis of the independence algorithm;
- ``source_graph_corrections`` — operator corrections of the graph
  (``merge`` / ``split``) with provenance, actor, rules version and
  validity (§11.3: a correction changes the classification but is not
  evidence for a claim; an operator attestation never SPLITS a group —
  only a correction with a verifiable chain does);
- the ``source_graph`` domain revision (the graph's version, the
  analogue of ``dependency_graph`` from T4.1).

Downgrade reverses the order.
"""

from __future__ import annotations

from alembic import op

revision = "0011_source_graph"
down_revision = "0010_env_independence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_dependency_edges (
            id                uuid PRIMARY KEY,
            from_source_id    uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            to_source_id      uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            kind              text NOT NULL
                              CHECK (kind IN ('link_to_primary','derived_from',
                                              'quote_of','republish_of')),
            basis_artifact_id uuid REFERENCES artifacts(id) ON DELETE SET NULL,
            origin            text NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_source_dependency_edges
            ON source_dependency_edges (from_source_id, to_source_id, kind, origin)
        """
    )

    op.execute(
        """
        CREATE TABLE source_graph_corrections (
            id                      uuid PRIMARY KEY,
            actor                   text NOT NULL,
            kind                    text NOT NULL CHECK (kind IN ('merge','split')),
            from_source_id          uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            to_source_id            uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            basis_artifact_id       uuid REFERENCES artifacts(id) ON DELETE SET NULL,
            rules_version           text NOT NULL,
            valid                   boolean NOT NULL DEFAULT true,
            reason_audit_event_id   uuid,
            created_at              timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_source_graph_corrections
            ON source_graph_corrections (actor, from_source_id, to_source_id, kind,
                                         rules_version)
        """
    )
    op.execute(
        "CREATE INDEX ix_source_graph_corrections_pair "
        "ON source_graph_corrections (from_source_id, to_source_id)"
    )

    # the source-graph cascade writes heads as a system actor: extend
    # the closed prepared_by list (T4.2 pattern, 0007)
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker',"
        "'system:cascade','system:barrier','system:source_graph'))"
    )

    # the source graph gets its own version (T4.1 pattern): a correction
    # or a new source bumps it; the algorithm's snapshot rows pin the
    # version they were computed under
    op.execute(
        "ALTER TABLE domain_revisions DROP CONSTRAINT domain_revisions_scope_check"
    )
    op.execute(
        "ALTER TABLE domain_revisions "
        "ADD CONSTRAINT domain_revisions_scope_check "
        "CHECK (scope IN ('knowledge','dependency_graph','source_graph'))"
    )
    op.execute(
        "INSERT INTO domain_revisions (scope, revision) VALUES ('source_graph', 0)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker',"
        "'system:cascade','system:barrier'))"
    )
    op.execute("DELETE FROM domain_revisions WHERE scope = 'source_graph'")
    op.execute(
        "ALTER TABLE domain_revisions DROP CONSTRAINT domain_revisions_scope_check"
    )
    op.execute(
        "ALTER TABLE domain_revisions "
        "ADD CONSTRAINT domain_revisions_scope_check "
        "CHECK (scope IN ('knowledge','dependency_graph'))"
    )
    op.execute("DROP TABLE source_graph_corrections")
    op.execute("DROP TABLE source_dependency_edges")
