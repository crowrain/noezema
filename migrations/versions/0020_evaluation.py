"""T7.5 (stage 7, §22.2): evaluation run — frozen config + gates.

Additive:

1. ``evaluation_runs`` — one row per evaluation run. A run is a series
   of 50–100 eligible sessions on a FROZEN config (model + config
   snapshot + rules), with the thresholds fixed before the series
   («Evaluation thresholds фиксируются до серии», §16.3). The run
   records the frozen config (config_snapshot_id, model_fingerprint,
   rules_version, rules_hash), the session window (started_at,
   finished_at), the gate outcomes (passed/failed/insufficient_sample
   per gate, §22.2), and the blind sample (seed, size, stratification).

   The table is the durable record of the full v1 acceptance gate:
   «прохождение §22.2 завершает full v1 acceptance» (§19 этап 7).
   ``insufficient_sample`` is neither pass nor fail — it means the
   measurement did not happen (denominator < 20); the reaction is to
   extend the run or, before freeze, exclude the claim type by a
   separate ADR.

Revision ID: 0020_evaluation
Revises: 0019_gc
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020_evaluation"
down_revision: str | None = "0019_gc"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evaluation_runs (
            id                    uuid PRIMARY KEY,
            label                 text NOT NULL,
            config_snapshot_id    uuid NOT NULL REFERENCES config_snapshots(id),
            model_fingerprint     jsonb NOT NULL,
            rules_version         text NOT NULL,
            rules_hash            text NOT NULL,
            thresholds            jsonb NOT NULL,
            started_at            timestamptz NOT NULL,
            finished_at           timestamptz,
            eligible_sessions     integer NOT NULL DEFAULT 0,
            completed_sessions    integer NOT NULL DEFAULT 0,
            gates                 jsonb NOT NULL DEFAULT '{}'::jsonb,
            blind_sample_seed     bigint NOT NULL,
            blind_sample_size     integer NOT NULL,
            outcome               text NOT NULL DEFAULT 'running'
                                 CHECK (outcome IN ('running', 'passed', 'failed',
                                    'insufficient_sample')),
            created_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_evaluation_runs_started ON evaluation_runs (started_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_evaluation_runs_started")
    op.execute("DROP TABLE IF EXISTS evaluation_runs")
