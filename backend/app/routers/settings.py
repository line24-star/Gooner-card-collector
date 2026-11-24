"""App settings. Media folder:

GET    /api/settings/media-folder          -> {path, default, overridden, exists, media_count,
                                              saved_unavailable (saved folder not in use, or null)}
PUT    /api/settings/media-folder {path, confirm?}
                                           -> 200 (same payload) | 409 {detail: {needs_confirm: true, media_count}}
                                              | 422 {detail: {message}}
POST   /api/settings/media-folder/open     -> 204 | 500 {detail: {message}}
POST   /api/settings/media-folder/pick     -> {path} from the system "Select folder" dialog (null when
                                              cancelled) | 409 a dialog is already open | 422 | 500.
                                              Only picks: the client then PUTs the path.
DELETE /api/settings/media-folder?confirm= -> reset to default; same payload/409 rules as PUT
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import desktop, folders

router = APIRouter(prefix="/api/settings", tags=["settings"])


class FolderIn(BaseModel):
    path: str
    confirm: bool = False


def _payload(db: Session) -> dict:
    cur = folders.current_media_dir()
    return {
        "path": str(cur),
        "default": str(folders.default_media_dir()),
        "overridden": cur != folders.default_media_dir(),
        "exists": cur.is_dir(),
        "media_count": folders.media_count(db),
        "saved_unavailable": folders.saved_unavailable(db),
    }


def _run(db: Session, fn) -> dict:
    try:
        fn()
    except folders.FolderChangeNeedsConfirm as e:
        raise HTTPException(409, detail={"needs_confirm": True, "media_count": e.count}) from None
    except ValueError as e:
        raise HTTPException(422, detail={"message": str(e)}) from None
    return _payload(db)


@router.get("/media-folder")
def get_media_folder(db: Session = Depends(get_db)) -> dict:
    return _payload(db)


@router.put("/media-folder")
def put_media_folder(body: FolderIn, db: Session = Depends(get_db)) -> dict:
    return _run(db, lambda: folders.set_media_dir(db, body.path, confirm=body.confirm))


@router.delete("/media-folder")
def delete_media_folder(confirm: bool = False, db: Session = Depends(get_db)) -> dict:
    return _run(db, lambda: folders.reset_media_dir(db, confirm=confirm))


@router.post("/media-folder/open", status_code=204)
def open_media_folder() -> Response:
    try:
        folders.open_media_dir()
    except RuntimeError as e:
        raise HTTPException(500, detail={"message": str(e)}) from None
    return Response(status_code=204)


@router.post("/media-folder/pick")
def pick_media_folder() -> dict:
    """Blocks (in a worker thread) until the player closes the dialog."""
    try:
        chosen = folders.pick_media_dir()
    except desktop.DialogBusy:
        raise HTTPException(409, detail={"message": "The folder dialog is already open. Look for it on your taskbar."}) from None
    except ValueError as e:
        raise HTTPException(422, detail={"message": str(e)}) from None
    except RuntimeError as e:
        raise HTTPException(500, detail={"message": str(e)}) from None
    return {"path": str(chosen) if chosen is not None else None}
