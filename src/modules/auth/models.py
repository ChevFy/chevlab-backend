import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from core.models import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(25), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
