"""T4.5 — online activation scaffolding (§8.7.2).

1. One unfinished ONLINE candidate per (base, payload) — the same
   deterministic identity as the offline partial unique (0001), terminal
   states excluded so an audited retry after ``failed`` (and a re-request
   of a payload that was active/superseded) can create a new row.
2. ``config_snapshots.repair_admission`` — the scheduler's
   ``T_repair_admission`` / repair SLO thresholds (T4.4 pattern: a
   validated section of the effective config, fail-closed).
3. The sealed-interval guard (§8.7.1/§8.7.2): while a candidate is in a
   pre-publish sealed state (``ready``, and for online also
   ``publishing``) its shadow heads are immutable — enforced by a DB
   trigger (the activation write predicate is the second, service-level
   half). Rebuild before the flip first conditionally returns
   ``ready → preparing_heads`` (which the trigger allows).

The runtime_config_heads activation slot (activating candidate, fence,
lease) and the config_snapshots post_publish fields already exist since
0001; this migration adds nothing to the slot.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0009_online_activation"
down_revision = "0008_writer_admission"
branch_labels = None
depends_on = None

from packages.domain.canonical import canonical_json_bytes  # noqa: E402
from packages.domain.config import BOOTSTRAP_PAYLOAD  # noqa: E402

_TRIG_FUNC = "noezema_guard_sealed_heads"


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_config_snapshots_online_candidate
            ON config_snapshots (base_snapshot_id, payload_sha256)
            WHERE activation_mode = 'online'
              AND activation_state NOT IN ('failed', 'active', 'superseded')
        """
    )

    op.execute(
        """
        ALTER TABLE config_snapshots
        ADD COLUMN repair_admission jsonb NOT NULL DEFAULT '{}'
        """
    )
    # backfill the bootstrap row with the pinned thresholds (bind param:
    # colons in JSON cannot sit in an op.execute literal)
    admission = canonical_json_bytes(BOOTSTRAP_PAYLOAD["repair_admission"]).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET repair_admission = CAST(:ra AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"ra": admission},
    )

    op.execute(
        f"""
        CREATE FUNCTION {_TRIG_FUNC}() RETURNS trigger AS $$
        BEGIN
            IF (TG_OP = 'DELETE' AND OLD.config_snapshot_id IN (
                   SELECT id FROM config_snapshots
                   WHERE activation_mode <> 'bootstrap'
                     AND activation_state IN ('ready', 'publishing')))
               OR (TG_OP IN ('INSERT', 'UPDATE') AND NEW.config_snapshot_id IN (
                   SELECT id FROM config_snapshots
                   WHERE activation_mode <> 'bootstrap'
                     AND activation_state IN ('ready', 'publishing'))) THEN
                RAISE EXCEPTION 'sealed activation heads are immutable '
                    '(candidate %)', COALESCE(
                        NEW.config_snapshot_id, OLD.config_snapshot_id)
                    USING ERRCODE = '45000';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_claim_assessment_heads_sealed
        BEFORE INSERT OR UPDATE OR DELETE ON claim_assessment_heads
        FOR EACH ROW EXECUTE FUNCTION {_TRIG_FUNC}()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_claim_assessment_heads_sealed "
        "ON claim_assessment_heads"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIG_FUNC}()")
    op.execute(
        "ALTER TABLE config_snapshots DROP COLUMN IF EXISTS repair_admission"
    )
    op.execute("DROP INDEX IF EXISTS uq_config_snapshots_online_candidate")
