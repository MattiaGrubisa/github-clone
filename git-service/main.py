import os
import shutil
import time
import git
import asyncio
import httpx

from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel


app = FastAPI(title = "Git Service")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
REPOS_ROOT = Path("/repos")
REPO_SERVICE_URL = os.getenv("REPO_SERVICE_URL", "http://repo-service:8000")
MAX_RETRIES = 3
BASE_DELAY_MS = 100
TIMING = os.getenv("TIMING") == "1"

security = HTTPBearer()
http_client = httpx.AsyncClient(timeout=3.0)

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

async def check_repo_access(repo_id: str, token: str, need_write: bool = False) -> None:
    url = f"{REPO_SERVICE_URL}/internal/repositories/{repo_id}/access"

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.monotonic_ns()
        try:
            response = await http_client.get(url, headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError:
            if TIMING:
                print(f"[timing] provjera pristupa (pokušaj {attempt}): {(time.monotonic_ns() - start) / 1_000_000:.2f} ms - connection error", flush=True)
            if attempt == MAX_RETRIES:
                raise HTTPException(status_code=503, detail="Authorization service unavailable")
            await asyncio.sleep(BASE_DELAY_MS * (2 ** (attempt - 1)) / 1000)
            continue

        if TIMING:
            print(f"[timing] provjera pristupa (pokušaj {attempt}): {(time.monotonic_ns() - start) / 1_000_000:.2f} ms", flush=True)

        if response.status_code == 503:
            if attempt == MAX_RETRIES:
                raise HTTPException(status_code=503, detail="Authorization check failed")
            print(f"[retry] pokušaj {attempt}/{MAX_RETRIES} nakon 503", flush=True)
            await asyncio.sleep(BASE_DELAY_MS * (2 ** (attempt - 1)) / 1000)
            continue

        # 200, 404, ili neki drugi status koji ne retryamo
        break

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository not found")

    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Authorization check failed")

    permissions = response.json()
    allowed = permissions["can_write"] if need_write else permissions["can_read"]

    if not allowed:
        raise HTTPException(status_code=404, detail="Repository not found")

@app.get("/health")
def health_check():
    if not REPOS_ROOT.exists():
        raise HTTPException(status_code = 503, detail = "Database disconnected.")
    return {"status": "healthy", "storage": "available"}

@app.post("/repos/init", status_code=201)
async def init_repository(data: InitRequest, user: dict = Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(security)):
    await check_repo_access(data.repo_id, credentials.credentials, need_write = True)
    path = repo_path(data.repo_id)

    if path.exists():
        raise HTTPException(status_code=409, detail="Repository already initialized")

    start = time.monotonic_ns()
    try:
        await asyncio.to_thread(git.Repo.init, path, bare=True)
    except git.GitCommandError:
        shutil.rmtree(path, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Failed to initialize repository")
    if TIMING:
        print(f"[timing] git init: {(time.monotonic_ns() - start) / 1_000_000:.2f} ms", flush=True)
    return {"repo_id": data.repo_id, "initialized": True}

def read_branches(path: Path) -> list[dict]:
    repo = git.Repo(path)
    return [
        {"name": head.name, "commit": head.commit.hexsha}
        for head in repo.heads
    ]

@app.get("/repos/{repo_id}/branches")
async def list_branches(repo_id: str, user: dict = Depends(get_current_user),
                        credentials: HTTPAuthorizationCredentials = Depends(security)):
    await check_repo_access(repo_id, credentials.credentials)
    path = repo_path(repo_id)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Repository not found")

    return await asyncio.to_thread(read_branches, path)

def read_commits(path: Path, branch: str, limit: int) -> list[dict]:
    repo = git.Repo(path)

    if branch not in repo.heads:
        raise HTTPException(status_code=404, detail="Branch not found")

    return [
        {
            "sha": commit.hexsha,
            "message": commit.message.strip(),
            "author": commit.author.name,
            "date": commit.committed_datetime.isoformat(),
        }
        for commit in repo.iter_commits(branch, max_count=limit)
    ]

@app.get("/repos/{repo_id}/commits")
async def list_commits(repo_id: str, branch: str = "master", limit: int = 20,
                        user: dict = Depends(get_current_user),
                        credentials: HTTPAuthorizationCredentials = Depends(security)):
    await check_repo_access(repo_id, credentials.credentials)
    path = repo_path(repo_id)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Repository not found")

    return await asyncio.to_thread(read_commits, path, branch, limit)

def read_tree(path: Path, branch: str, path_prefix: str) -> list[dict]:
    repo = git.Repo(path)

    if branch not in repo.heads:
        raise HTTPException(status_code=404, detail="Branch not found")

    tree = repo.heads[branch].commit.tree

    if path_prefix:
        try:
            tree = tree / path_prefix
        except KeyError:
            raise HTTPException(status_code=404, detail="Path not found")

    return [
        {"name": item.name, "type": item.type, "size": item.size}
        for item in tree
    ]

@app.get("/repos/{repo_id}/tree")
async def list_tree(repo_id: str, branch: str = "master", path_prefix: str = "",
                    user: dict = Depends(get_current_user),
                    credentials: HTTPAuthorizationCredentials = Depends(security)):
    await check_repo_access(repo_id, credentials.credentials)
    path = repo_path(repo_id)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Repository not found")

    return await asyncio.to_thread(read_tree, path, branch, path_prefix)