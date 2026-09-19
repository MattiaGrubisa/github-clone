import os
import psycopg2

from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from jose import jwt, JWTError
from passlib.context import CryptContext

app = FastAPI(title = "Auth Service")

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes = ["bcrypt"], deprecated = ["auto"])
security = HTTPBearer()

class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms = [ALGORITHM])
    except JWTError:
        raise HTTPException(status_code = 401, detail = "Invalid or expired token")

    return {"id": payload["sub"], "username": payload["username"]}

@app.get("/health")
def health_check():
    try:
        connection = get_db_connection()
        connection.close()
        return {"status" : "healthy", "database" : "connected"}
    except Exception:
        raise HTTPException(status_code = 503, detail = "Database disconnected.")

def create_access_token(user_id: str, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes = TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "username": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm = ALGORITHM)

@app.post("/register", status_code = 201)
def register(data: RegisterRequest):
    password_hash = pwd_context.hash(data.password)
    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, email, password_hash) "
                "VALUES (%s, %s, %s) RETURNING id",
                (data.username, data.email, password_hash),
            )
            user_id = cur.fetchone()[0]
        con.commit()
    except psycopg2.errors.UniqueViolation:
        con.rollback()
        raise HTTPException(status_code = 409, detail = "Username or email already taken.")
    finally:
        con.close()

    return {"id": str(user_id), "username": data.username}

@app.post("/login")
def login(data: LoginRequest):
    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash FROM users WHERE username = %s",
                (data.username,),
            )
            row = cur.fetchone()
    finally:
        con.close()

    if row is None or not pwd_context.verify(data.password, row[1]):
        raise HTTPException(status_code = 401, detail = "Invalid credentials")

    token = create_access_token(row[0], data.username)
    return {"access_token": token, "token_type": "bearer"}

@app.delete("/account", status_code = 204)
def delete_account(user: dict = Depends(get_current_user)):
    con = get_db_connection()
    try:
        with con.cursor() as cur:
            cur.execute(
                "DELETE FROM users WHERE id = %s RETURNING id",
                (user["id"],),
            )
            row = cur.fetchone()
        con.commit()
    finally:
        con.close()

    if row is None:
        raise HTTPException(status_code = 404, detail = "User not found")