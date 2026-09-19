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

def get_db_conection():
    return psycopg2.connect(DATABASE_URL)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms = [ALGORITHM])
    except JWTError:
        raise HTTPException(status_code = 401, detail = "Invalid or expired token")

    return {"id": payload["sub"], "username": payload["username"]}

class RepositoryCreate(BaseModel):
    name: str
    description: str | None = None
    is_private: bool = False

class RepositoryUpdate(BaseModel):
    description: str | None = None
    is_private: bool = False

@app.get("/health")
def health_check():
    try:
        con = get_db_conection()
        con.close()
        return {"status": "healthy", "databese": "conected"}
    except Exception:
        raise HTTPException(status_code = 503, detail = "Database disconnected.")

@app.post("/repositories", status_code = 201)
def create_repo(data: RepositoryCreate, user: dict = Depends(get_current_user)):
    con = get_db_conection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO repositories (name, description, owner_id, is_private) "
                "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
                (data.name, data.description, user["id"], data.is_private),
            )
            repo_id, created_at = cur.fetchone()
        con.commit()
    except psycopg2.errors.UniqueViolation:
        con.rollback()
        raise HTTPException(status_code = 409, detail = "Repository with that name already exists")
    finally:
        con.close()

    return {
        "id": str(repo_id),
        "name": data.name,
        "description": data.description,
        "is_private": data.is_private,
        "owner": user["username"],
        "create_at": created_at
    }

@app.get("/repositories")
def list_repos(user: dict = Depends(get_current_user)):
    con = get_db_conection()
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

@app.get("/repositories/{repo_id}")
def get_repo(repo_id: str, user: dict = Depends(get_current_user)):
    con = get_db_conection()
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

    con = get_db_conection()
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
    con = get_db_conection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "DELETE FROM repositories WHERE id = %s AND owner_id = %s RETURNING id",
                (repo_id, user["id"]),
            )
            row = cur.fetchone()
        con.commit()
    finally:
        con.close()

    if row is None:
        raise HTTPException(status_code = 404, detail = "Repository not found")

@app.get("/internal/repositories/{repo_id}/access")
def check_access(repo_id: str, user: dict = Depends(get_current_user)):
    con = get_db_conection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT owner_id, is_private FROM repositories WHERE id = %s",
                (repo_id,),
            )
            row = cur.fetchone()
    finally:
        con.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    is_owner = str(row[0]) == user["id"]
    is_private = row[1]

    return {
        "can_read": is_owner or not is_private,
        "can_write": is_owner,
    }