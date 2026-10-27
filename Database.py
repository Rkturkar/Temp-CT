"""Database layer: engine, ORM models, schema setup, and dataset loading.

Uses SQLite by default so the demo runs with no server. To use MySQL 8, set
DATABASE_URL, for example:
    mysql+pymysql://USER:PASSWORD@HOST:3306/cold_chain
All timestamps are stored as naive UTC datetimes.
"""

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker,
)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'cold_chain.db'}")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    min_temp_c: Mapped[float] = mapped_column(Float)
    max_temp_c: Mapped[float] = mapped_column(Float)
    max_excursion_min: Mapped[int] = mapped_column(Integer)
    max_gap_min: Mapped[int] = mapped_column(Integer)
    accuracy_c: Mapped[float] = mapped_column(Float)


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    origin: Mapped[str] = mapped_column(String(120))
    destination: Mapped[str] = mapped_column(String(120))
    departed_at: Mapped[datetime] = mapped_column(DateTime)
    arrived_at: Mapped[datetime] = mapped_column(DateTime)

    product: Mapped[Product] = relationship()
    readings: Mapped[list["Reading"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan", order_by="Reading.recorded_at",
    )
    custody: Mapped[list["CustodyEvent"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan", order_by="CustodyEvent.seq",
    )


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("shipment_id", "recorded_at", name="uq_reading_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    temperature_c: Mapped[float] = mapped_column(Float)

    shipment: Mapped[Shipment] = relationship(back_populates="readings")


class CustodyEvent(Base):
    __tablename__ = "custody_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(20))
    party: Mapped[str] = mapped_column(String(80))
    location: Mapped[str] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)

    shipment: Mapped[Shipment] = relationship(back_populates="custody")


def init_db() -> None:
    Base.metadata.create_all(engine)


def reset_db() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def load_dataset(db: Session, data: dict) -> None:
    """Insert products, shipments, readings and custody events in one transaction."""
    products: dict[str, Product] = {}
    for p in data["products"]:
        row = Product(**p)
        db.add(row)
        products[p["code"]] = row

    for s in data["shipments"]:
        ship = Shipment(
            code=s["code"],
            product=products[s["product_code"]],
            origin=s["origin"],
            destination=s["destination"],
            departed_at=s["departed_at"],
            arrived_at=s["arrived_at"],
        )
        ship.readings = [Reading(recorded_at=ts, temperature_c=t) for ts, t in s["readings"]]
        ship.custody = [CustodyEvent(**c) for c in s["custody"]]
        db.add(ship)

    db.commit()