import os
import psycopg2
from fastapi import FastAPI

app = FastAPI(title = "Auth Service")

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.get("/health")
def health_check():
    try:
        connection = get_db_connection()
        connection.close()
        return {"status" : "healthy", "database" : "connected"}
    except Exception:
        return {"status" : "unhealthy", "database" : "disconnected"}