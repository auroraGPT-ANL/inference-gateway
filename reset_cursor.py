import psycopg2
from psycopg2 import sql
import os

# Fetch database connection settings from environment variables
db_name = os.getenv('PGDATABASE', 'default_db_name')
db_user = os.getenv('PGUSER', 'default_user')
db_password = os.getenv('PGPASSWORD', '')  # This will be empty since .pgpass will handle the password
db_host = os.getenv('PGHOST', 'localhost')
db_port = os.getenv('PGPORT', '5432')

# Connect to the PostgreSQL database
conn = psycopg2.connect(
    dbname=db_name, user=db_user, password=db_password, host=db_host, port=db_port
)
cur = conn.cursor()

# Fetch all tables in the public schema
cur.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
""")
tables = cur.fetchall()

# Reset sequences for all tables
for table in tables:
    table_name = table[0]
    
    # Try to reset the sequence for the primary key column
    try:
        reset_query = sql.SQL("""
            SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id)+1, 1), false)
            FROM {}
        """).format(sql.Identifier(table_name))
        
        cur.execute(reset_query, (table_name,))
        print(f"Reset sequence for table {table_name}")
    
    except Exception as e:
        print(f"Could not reset sequence for table {table_name}: {e}")

# Commit the changes and close the connection
conn.commit()
cur.close()
conn.close()
