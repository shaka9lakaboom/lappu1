"""
Database abstraction layer foundation (P0)
Prepares structure for Supabase PostgreSQL connection & pgvector operations.
"""
from typing import Optional


class DatabaseSessionPlaceholder:
    def __init__(self, connection_url: Optional[str] = None):
        self.connection_url = connection_url

    def is_connected(self) -> bool:
        return bool(self.connection_url)


db_session = DatabaseSessionPlaceholder()
