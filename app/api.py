"""HTTP routes under /api.

POST /api/upload        201 UploadOut, Location: /api/uploads/{id}
GET  /api/uploads/{id}  200 UploadOut, or 404
GET  /api/health        200 HealthOut, or 503
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api")
