from __future__ import annotations

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory, create_all
from app.seed import seed_all


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    if settings.auto_create_tables:
        create_all(engine)
    result = seed_all(build_session_factory(engine))
    print(f"seed complete: {result}")


if __name__ == "__main__":
    main()
