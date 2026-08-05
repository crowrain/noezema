import uuid
from datetime import datetime, timedelta

from packages.domain.models.session import Session
from packages.domain.models.enums import SessionState


class SessionService:
    def __init__(self, db):
        self.db = db

    async def create_session(self, config_snapshot_id: uuid.UUID) -> Session:
        lease_duration = timedelta(minutes=30)
        session = Session(
            config_snapshot_id=config_snapshot_id,
            state=SessionState.WAKING,
            lease_owner="orchestrator",
            lease_expires_at=datetime.now() + lease_duration,
            last_heartbeat_at=datetime.now(),
            last_progress_at=datetime.now(),
        )
        return session

    async def transition(self, session: Session, new_state: SessionState):
        session.state = new_state
        session.updated_at = datetime.now()

    async def heartbeat(self, session: Session) -> bool:
        if session.is_terminal:
            return False
        if session.phase_deadline and datetime.now() > session.phase_deadline:
            return False
        session.last_heartbeat_at = datetime.now()
        return True
