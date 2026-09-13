import os

from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
from jose import jwt, JWTError

app = FastAPI(title="Repository Service")

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

security = HTTPBearer()

def get_db_connection():
    return psycopg2.conect(DATABASE_URL)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms = [ALGORITHM])
    except JWTError:
        raise HTTPException(status_code = 401, detail = "Invalid or expired token")

    return {id: payload["sub"], "username": payload["username"]}

class RepositoryCreate(BaseModel):
    name: str
    description: str | None = None
    is_privete: bool = False

class RepositoryUpdate(BaseModel):
    description: str | None = None
    is_privete: bool = False

@app.get("/healt")
def health_check():
    try:
        con = get_db_connection()
        con.close()
        return {"status": "healthy", "databese": "conected"}
    except Exception:
        return {"status": "unhealthy", "databse": "disconected"}

@app.post("/repositories", status_code = 201)
def create_repo(data: RepositoryCreate, user: dict = Depends(get_current_user)):
    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO repositories (name, description, owner_id, is_private) "
                "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
                (data.name, data.description, user["id"], data.is_private),
            )
            repo_id, create_at = cur.fetchone()
        con.commit()
    except psycopg2.errors.UniqueViolation:
        con.rollback()
        raise HTTPException(status_code = 409, detail = "Repository with that name already exists")
    finally:
        con.close()

@app.get("/repositories")
def list_repos(user: dict = Depends(get_current_user)):
    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, is_private, created_at "
                "FROM repositories WHERE owner_id = %s ORDER BY created_at DESC",
                (user["id"],),
            )
            rows = cur.fetchall()
    finally:
        con.close()

    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "description": row[2],
            "is_private": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]

@app.get("/repositories/{repo_ide}")
def get_repo(repo_id: str, user: dict = Depends(get_current_user)):
    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, is_private, owner_id, created_at "
                "FROM repositories WHERE id = %s",
                (repo_id,),
            )
            row = cur.fetchone()
    finally:
        con.close()

    if row is None:
        raise HTTPException(status_code = 404, detail = "Repository not found")

    if row[3] and str(row[4]) != user["id"]:
        raise HTTPException(status_code = 404, detail = "Repository not found")

    return {
        "id": str(row[0]),
        "name": row[1],
        "description": row[2],
        "is_private": row[3],
        "created_at": row[5],
    }

@app.patch("/repositories/{repo_id}")
def update_repo(repo_id: str, data: RepositoryUpdate, user: dict = Depends(get_current_user)):
    fields = data.model_dump(exclude_unset = True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    assignments = ", ".join(f"{key} = %s" for key in fields)
    values = list(fields.values()) + [repo_id, user["id"]]

    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                f"UPDATE repositories SET {assignments} "
                "WHERE id = %s AND owner_id = %s RETURNING id",
                values,
            )
            row = cur.fetchone()
        con.commit()
    finally:
        con.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    return {"id": str(row[0]), "updated": list(fields.keys())}

@app.delete("/repositories/{repo_id}", status_code = 204)
def delete_repository(repo_id: str, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM repositories WHERE id = %s AND owner_id = %s RETURNING id",
                (repo_id, user["id"]),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code = 404, detail = "Repository not found")