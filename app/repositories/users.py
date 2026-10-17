"""User persistence. No business rules here."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: uuid.UUID) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, normalized_email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == normalized_email)
        return self.db.scalars(stmt).one_or_none()

    def add(self, *, email: str, password_hash: str, display_name: str) -> User:
        user = User(email=email, password_hash=password_hash, display_name=display_name)
        self.db.add(user)
        self.db.flush()
        return user

    def set_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash

    def mark_login(self, user: User, when: datetime) -> None:
        user.last_login_at = when
