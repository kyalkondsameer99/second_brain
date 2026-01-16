from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import settings

# pool_pre_ping avoids stale connections (common in container setups)
engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """FastAPI dependency providing a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
