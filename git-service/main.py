import os
import shutil
from pathlib import Path

import git
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel

app = FastAPI(title = "Git Service")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
REPOS_ROOT = Path("/repos")

security = HTTPBearer()

class InitRequest(BaseModel):
    repo_id: str

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms = [ALGORITHM])
    except JWTError:
        raise HTTPException(status_code = 401, detail = "Invalid or expired token")

    return {"id": payload["sub"], "username": payload["username"]}

def repo_path(repo_id: str) -> Path:
    path = (REPOS_ROOT / repo_id).resolve()
    if path.parent != REPOS_ROOT:
        raise HTTPException(status_code = 400, detail = "Invalid repository id")
    return path

@app.get("/health")
def health_check():
    if not REPOS_ROOT.exists():
        return {"status": "unhealthy", "storage": "unavailable"}
    return {"status": "healthy", "storage": "available"}

@app.post("/repos/init", status_code=201)
def init_repository(data: InitRequest, user: dict = Depends(get_current_user)):
    path = repo_path(data.repo_id)

    if path.exists():
        raise HTTPException(status_code=409, detail="Repository already initialized")

    try:
        git.Repo.init(path, bare=True)
    except git.GitCommandError:
        shutil.rmtree(path, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Failed to initialize repository")

    return {"repo_id": data.repo_id, "initialized": True}