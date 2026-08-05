"""Initial schema — all domain tables.

Revision ID: 001_initial
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('questions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('statement', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=32), server_default='seeded'),
        sa.Column('resolved', sa.Boolean(), server_default='f'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('sessions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('state', sa.String(length=32), server_default='created'),
        sa.Column('question_id', sa.Uuid(), sa.ForeignKey('questions.id')),
        sa.Column('config_snapshot_id', sa.Uuid()),
        sa.Column('lease_owner', sa.String(length=128)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True)),
        sa.Column('last_progress_at', sa.DateTime(timezone=True)),
        sa.Column('phase_deadline', sa.DateTime(timezone=True)),
        sa.Column('stop_requested_at', sa.DateTime(timezone=True)),
        sa.Column('abort_requested_at', sa.DateTime(timezone=True)),
        sa.Column('workspace_manifest_id', sa.Uuid()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('model_runs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('input_tokens', sa.Integer(), server_default='0'),
        sa.Column('output_tokens', sa.Integer(), server_default='0'),
        sa.Column('latency_ms', sa.Float(), server_default='0'),
        sa.Column('error', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('audit_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.Uuid()),
        sa.Column('payload', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('messages',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('sender', sa.String(length=128), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(length=16), server_default='normal'),
        sa.Column('state', sa.String(length=16), server_default='queued'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('actions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('session_id', sa.Uuid(), sa.ForeignKey('sessions.id')),
        sa.Column('tool', sa.String(length=64), nullable=False),
        sa.Column('arguments', sa.Text()),
        sa.Column('result', sa.Text()),
        sa.Column('status', sa.String(length=32), server_default='action_proposed'),
        sa.Column('step', sa.Integer(), server_default='0'),
        sa.Column('run_id', sa.Uuid()),
        sa.Column('turn_id', sa.Uuid()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('commit_attempts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('session_id', sa.Uuid(), sa.ForeignKey('sessions.id')),
        sa.Column('status', sa.String(length=32), server_default='prepared'),
        sa.Column('staging_hash', sa.String(length=64)),
        sa.Column('workspace_manifest_id', sa.Uuid()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('claims',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('statement', sa.Text(), nullable=False),
        sa.Column('claim_type', sa.String(length=32), nullable=False),
        sa.Column('freshness_status', sa.String(length=16), server_default='fresh'),
        sa.Column('valid_from', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('valid_to', sa.DateTime(timezone=True)),
        sa.Column('reverify_after', sa.DateTime(timezone=True)),
        sa.Column('created_in_session', sa.Uuid()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('claim_dependencies',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('claim_id', sa.Uuid(), sa.ForeignKey('claims.id')),
        sa.Column('depends_on_id', sa.Uuid(), sa.ForeignKey('claims.id')),
        sa.Column('relation', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('evidence',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('claim_id', sa.Uuid(), sa.ForeignKey('claims.id')),
        sa.Column('relation', sa.String(length=16), nullable=False),
        sa.Column('evidence_kind', sa.String(length=32), nullable=False),
        sa.Column('identity_hash', sa.String(length=64), nullable=False),
        sa.Column('scope', sa.Text()),
        sa.Column('source_id', sa.Uuid()),
        sa.Column('created_in_session', sa.Uuid()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('counterevidence_resolutions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('claim_id', sa.Uuid(), sa.ForeignKey('claims.id')),
        sa.Column('evidence_id', sa.Uuid(), sa.ForeignKey('evidence.id')),
        sa.Column('actor', sa.String(length=32), nullable=False),
        sa.Column('basis', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('claim_assessments',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('claim_id', sa.Uuid(), sa.ForeignKey('claims.id')),
        sa.Column('effective_grade', sa.String(length=4), nullable=False),
        sa.Column('epistemic_status', sa.String(length=16)),
        sa.Column('rules_version', sa.Integer(), server_default='1'),
        sa.Column('rules_hash', sa.String(length=64), nullable=False),
        sa.Column('evidence_set_hash', sa.String(length=64), nullable=False),
        sa.Column('confidence', sa.Float()),
        sa.Column('valid', sa.Boolean(), server_default='t'),
        sa.Column('created_in_session', sa.Uuid()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('claim_assessment_heads',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('claim_id', sa.Uuid(), sa.ForeignKey('claims.id'), unique=True),
        sa.Column('config_snapshot_id', sa.Uuid(), nullable=False),
        sa.Column('assessment_state', sa.String(length=16), server_default='current'),
        sa.Column('current_assessment_id', sa.Uuid()),
        sa.Column('epistemic_status', sa.String(length=16)),
        sa.Column('prepared_by', sa.String(length=32)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('config_snapshots',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('rules_hash', sa.String(length=64), nullable=False),
        sa.Column('activation_state', sa.String(length=32), server_default='active'),
        sa.Column('activating_candidate_id', sa.Uuid()),
        sa.Column('scope', sa.String(length=32), server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table('domain_revisions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('scope', sa.String(length=32), unique=True, nullable=False),
        sa.Column('value', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('domain_revisions')
    op.drop_table('config_snapshots')
    op.drop_table('claim_assessment_heads')
    op.drop_table('claim_assessments')
    op.drop_table('counterevidence_resolutions')
    op.drop_table('evidence')
    op.drop_table('claim_dependencies')
    op.drop_table('claims')
    op.drop_table('commit_attempts')
    op.drop_table('actions')
    op.drop_table('messages')
    op.drop_table('audit_events')
    op.drop_table('model_runs')
    op.drop_table('sessions')
    op.drop_table('questions')
