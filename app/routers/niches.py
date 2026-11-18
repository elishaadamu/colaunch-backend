from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from app.auth import require_operator
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.niche import TargetNiche

router = APIRouter(prefix="/api/niches", tags=["niches"])


class NicheCreateSchema(BaseModel):
    name: str
    category: Optional[str] = "custom"
    is_active: Optional[bool] = True
    count: Optional[str] = "5.0k"


class NicheToggleSchema(BaseModel):
    name: str
    is_active: bool


class NicheActiveBulkSchema(BaseModel):
    niches: List[str]


@router.get("")
def list_niches(db: Session = Depends(get_db)):
    """List all available niches and active selected target niches from DB."""
    niches = db.query(TargetNiche).order_by(TargetNiche.name.asc()).all()
    # Default fallback ONLY if DB was just created or completely empty
    if not niches:
        DEFAULT_NICHES = [
            ("Tech", "tech", True, "14.2k"),
            ("Software", "tech", True, "9.8k"),
            ("SaaS", "tech", True, "6.4k"),
            ("Fintech", "business", True, "5.1k"),
            ("Productivity", "business", True, "11.3k"),
            ("AI Tools", "tech", False, "8.7k"),
            ("Creator Economy", "creative", False, "7.5k"),
            ("Gaming", "creative", False, "22.1k"),
            ("Fitness & Health", "lifestyle", False, "13.9k"),
            ("E-Commerce", "business", False, "8.2k"),
            ("Finance", "business", False, "6.9k"),
            ("Crypto & Web3", "business", False, "4.8k"),
            ("Design & Creative", "creative", False, "9.1k"),
            ("Education", "lifestyle", False, "10.5k"),
            ("Beauty & Lifestyle", "lifestyle", False, "16.7k"),
            ("Marketing", "business", False, "8.4k"),
        ]
        for name, cat, active, cnt in DEFAULT_NICHES:
            slug_id = name.lower().replace(" & ", "-").replace(" ", "-")
            db.add(TargetNiche(
                id=slug_id,
                name=name,
                category=cat,
                is_active=active,
                count=cnt,
            ))
        db.commit()
        niches = db.query(TargetNiche).order_by(TargetNiche.name.asc()).all()

    active_niches = [n.name for n in niches if n.is_active]
    return {
        "active_niches": active_niches,
        "all_niches": [
            {
                "id": n.id,
                "name": n.name,
                "label": n.name,
                "category": n.category or "custom",
                "is_active": bool(n.is_active),
                "count": n.count or "5.0k",
            }
            for n in niches
        ],
    }


@router.post("")
def add_niche(data: NicheCreateSchema, db: Session = Depends(get_db)):
    """Add a new target niche and save in DB."""
    name_clean = data.name.strip().replace(",", "")
    if not name_clean:
        raise HTTPException(400, "Niche name cannot be empty")

    existing = db.query(TargetNiche).filter(TargetNiche.name.ilike(name_clean)).first()
    if existing:
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return {
            "success": True,
            "niche": {
                "id": existing.id,
                "name": existing.name,
                "label": existing.name,
                "category": existing.category,
                "is_active": existing.is_active,
                "count": existing.count,
            },
        }

    slug_id = name_clean.lower().replace("&", "").replace(" ", "-").strip("-")
    new_niche = TargetNiche(
        id=slug_id or name_clean.lower(),
        name=name_clean,
        category=data.category or "custom",
        is_active=data.is_active if data.is_active is not None else True,
        count=data.count or "5.0k",
    )
    db.add(new_niche)
    db.commit()
    db.refresh(new_niche)
    return {
        "success": True,
        "niche": {
            "id": new_niche.id,
            "name": new_niche.name,
            "label": new_niche.name,
            "category": new_niche.category,
            "is_active": new_niche.is_active,
            "count": new_niche.count,
        },
    }


@router.delete("/{name}")
def remove_niche(name: str, permanent: bool = True, db: Session = Depends(get_db), _op=Depends(require_operator)):
    """Remove a niche permanently from the DB."""
    name_clean = name.strip()
    existing = db.query(TargetNiche).filter(
        (TargetNiche.name.ilike(name_clean)) | (TargetNiche.id == name_clean)
    ).first()

    if not existing:
        return {"success": True, "message": "Niche not found or already removed"}

    db.delete(existing)
    db.commit()
    return {"success": True, "removed": name_clean}


@router.put("/toggle")
def toggle_niche(data: NicheToggleSchema, db: Session = Depends(get_db)):
    """Toggle a niche's active status."""
    existing = db.query(TargetNiche).filter(TargetNiche.name.ilike(data.name.strip())).first()
    if not existing:
        slug_id = data.name.lower().replace("&", "").replace(" ", "-").strip("-")
        existing = TargetNiche(
            id=slug_id,
            name=data.name.strip(),
            category="custom",
            is_active=data.is_active,
            count="5.0k",
        )
        db.add(existing)
    else:
        existing.is_active = data.is_active
    db.commit()
    db.refresh(existing)
    return {"success": True, "name": existing.name, "is_active": existing.is_active}


@router.put("/active")
def update_active_niches(data: NicheActiveBulkSchema, db: Session = Depends(get_db)):
    """Sync the full list of active target niches."""
    active_set = {n.strip().lower() for n in data.niches if n.strip()}
    all_niches = db.query(TargetNiche).all()

    for n in all_niches:
        n.is_active = n.name.lower() in active_set

    # If any active niche was not in DB, insert it
    existing_names = {n.name.lower() for n in all_niches}
    for raw_name in data.niches:
        c_name = raw_name.strip()
        if c_name and c_name.lower() not in existing_names:
            slug_id = c_name.lower().replace("&", "").replace(" ", "-").strip("-")
            db.add(TargetNiche(
                id=slug_id or c_name.lower(),
                name=c_name,
                category="custom",
                is_active=True,
                count="5.0k",
            ))

    db.commit()
    return {"success": True, "active_niches": data.niches}
