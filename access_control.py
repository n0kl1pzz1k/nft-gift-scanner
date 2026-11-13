"""Central role and ownership policy. No financial mutation permission exists."""
from enum import Enum

from fastapi import HTTPException

class Role(str, Enum):
    BUYER = "buyer"
    SELLER = "seller"
    ADMIN = "admin"

def resolve_role(user: dict, admin_ids: set[str]) -> Role:
    user_id = str(user.get("id", ""))
    return Role.ADMIN if user_id and user_id in admin_ids else Role.BUYER

def require_role(user: dict, admin_ids: set[str], allowed: set[Role]) -> Role:
    role = resolve_role(user, admin_ids)
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient role permissions")
    return role

def require_owner(user: dict, owner_id: str) -> None:
    if str(user.get("id", "")) != str(owner_id):
        raise HTTPException(status_code=403, detail="Resource ownership required")
