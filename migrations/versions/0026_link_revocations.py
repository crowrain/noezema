"""0026: отзывы связей улики (T7.125, ADR-0039; минимальный честный вариант — ADR-0038 §13.4).

Ранее записанную `supports`-связь было нечем снять честно: у `evidence` нет признака
«исключена из оценки» (0004:74–99, правок её колонок не было), смена `relation` врёт об
опровержении, `counterevidence_resolutions` (0012) покрывает только `counters`,
`assessment_evidence.role` правилами не читается (0038 §13.2). Отзыв — отдельная
append-only сущность по образцу `counterevidence_resolutions`: она называет, кто и почему
снял улику с оценки, и правила видят отсутствие явно (ADR-0039).

Invariants:

- review of the link does not touch `evidence`: no UPDATE, no DELETE, no new column on
  that table — the claim's evidence rows stay in the journal untouched (§3 staging spirit);
- ``claim_id`` is a copy of ``evidence.claim_id`` for the per-claim read (the inter-row
  equality is checked by the service, the same way 0012 checks its basis rows);
- at most one ACTIVE revocation per evidence (partial unique on ``evidence_id`` where
  ``cancelled_at IS NULL``); a cancelled row stays for history and no longer excludes
  anything — cancellation is a second append, not an edit of history;
- the cancellation columns are consistent: ``cancelled_at``, ``cancelled_by`` and
  ``cancel_reason`` are set together and none of them alone (CHECK) — a row cannot be
  «cancelled by nobody at no time», and an cancellation cannot be left without a reason;
- ``reason`` is required and non-blank, ``basis`` carries the verifiable basis of the
  decision (the gate verdict, the value profile, the page text hash, the method and rules
  version) as recorded at the moment of revocation.

The revocation cascade writes heads as a system actor: extend the closed ``prepared_by``
list (the T4.2/T4.7/T4.8 pattern of 0007/0011/0012).
"""

from __future__ import annotations

from alembic import op

revision = "0026_link_revocations"
down_revision = "0025_prompt_content_pin"
branch_labels = None
depends_on = None

_PREPARED_BY_OLD = (
    "'session','rules_activation','reassessment_worker',"
    "'system:cascade','system:barrier','system:source_graph',"
    "'system:counter_resolution','commit_carryover'"
)
_PREPARED_BY_NEW = _PREPARED_BY_OLD + ",'system:link_revocation'"


def _replace_prepared_by_check(prepared_by: str) -> None:
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        f"CHECK (prepared_by IN ({prepared_by}))"
    )


def upgrade() -> None:
    _replace_prepared_by_check(_PREPARED_BY_NEW)

    op.execute(
        """
        CREATE TABLE evidence_link_revocations (
            id            uuid PRIMARY KEY,
            evidence_id   uuid NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
            claim_id      uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            reason        text NOT NULL CHECK (btrim(reason) <> ''),
            basis         jsonb NOT NULL DEFAULT '{}'::jsonb,
            actor         text NOT NULL,
            rules_version text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            cancelled_at  timestamptz,
            cancelled_by  text,
            cancel_reason text,
            CHECK (
                (cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)
                OR
                (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL
                    AND cancel_reason IS NOT NULL AND btrim(cancel_reason) <> '')
            )
        )
        """
    )
    # at most one ACTIVE revocation per evidence; cancelled rows stay for history
    op.execute(
        """
        CREATE UNIQUE INDEX uq_link_revocations_active
        ON evidence_link_revocations (evidence_id) WHERE cancelled_at IS NULL
        """
    )
    op.execute(
        "CREATE INDEX ix_link_revocations_evidence ON evidence_link_revocations (evidence_id)"
    )
    op.execute(
        "CREATE INDEX ix_link_revocations_claim ON evidence_link_revocations (claim_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE evidence_link_revocations")
    _replace_prepared_by_check(_PREPARED_BY_OLD)
