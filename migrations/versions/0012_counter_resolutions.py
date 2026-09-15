"""0012: counterevidence resolutions (T4.8, §8.7.4, §14).

A resolution is a SEPARATE audited entity, not a model flag (§8.7.4):
it records that one ``counters`` evidence no longer counts against the
claim, with exactly one verifiable basis — either an evidence row (the
counterexample is out of the claim scope / a method error is shown / a
new evidence explains the divergence) or a valid source-graph
correction (the provenance changed). Invariants:

- XOR: exactly one of ``basis_evidence_id`` / ``basis_correction_id``
  is set (DB CHECK, spec line 2141);
- at most one VALID resolution per target evidence (partial unique,
  spec line 2142);
- the target evidence belongs to the same claim and participates as
  ``counters``; the basis is currently valid, scope-compatible and not
  transitively dependent on the target — the inter-row invariants are
  checked by the service (deferred trigger / rules engine, §14);
- ``basis_correction_id`` references a VALID ``source_graph_corrections``
  row; invalidating the basis makes the resolution invalid and cascades
  the recomputation of the assessment that counted the counterevidence
  resolved (§8.7.4, §20.11).

The counter-resolution cascade writes heads as a system actor: extend
the closed ``prepared_by`` list (T4.2/T4.7 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "0012_counter_resolutions"
down_revision = "0011_source_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker',"
        "'system:cascade','system:barrier','system:source_graph',"
        "'system:counter_resolution'))"
    )

    op.execute(
        """
        CREATE TABLE counterevidence_resolutions (
            id                    uuid PRIMARY KEY,
            evidence_id           uuid NOT NULL REFERENCES evidence(id),
            basis_evidence_id     uuid REFERENCES evidence(id),
            basis_correction_id   uuid REFERENCES source_graph_corrections(id),
            actor                 text NOT NULL,
            rules_version         text NOT NULL,
            reason_audit_event_id uuid,
            valid                 boolean NOT NULL DEFAULT true,
            created_in_session    uuid REFERENCES sessions(id) ON DELETE SET NULL,
            created_at            timestamptz NOT NULL DEFAULT now(),
            CHECK ((basis_evidence_id IS NULL) <> (basis_correction_id IS NULL))
        )
        """
    )
    # at most one VALID resolution per target evidence (spec line 2142);
    # invalidated rows stay for history
    op.execute(
        """
        CREATE UNIQUE INDEX uq_counter_resolutions_valid
        ON counterevidence_resolutions (evidence_id) WHERE valid
        """
    )
    op.execute(
        "CREATE INDEX ix_counter_resolutions_basis_evidence "
        "ON counterevidence_resolutions (basis_evidence_id)"
    )
    op.execute(
        "CREATE INDEX ix_counter_resolutions_basis_correction "
        "ON counterevidence_resolutions (basis_correction_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE counterevidence_resolutions")
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
