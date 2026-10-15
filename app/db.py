#config supabase python SDK client and direct PSQL connection via psycopg2

from supabase import create_client, Client
import psycopg2
from psycopg2.extras import RealDictCursor
from app.config import settings

# Supabase Client Initialization
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# Direct PostgreSQL Connection
def get_db_connection():
    try:
        conn = psycopg2.connect(
            settings.DATABASE_URL,
            cursor_factory=RealDictCursor
        )
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        raise e