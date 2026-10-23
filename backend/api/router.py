"""Mounts all API routers."""

from fastapi import APIRouter

from api import auth, flow, generate, insert, progress, record, render, script

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(record.router)
api_router.include_router(flow.router)
api_router.include_router(insert.router)
api_router.include_router(generate.router)
api_router.include_router(render.router)
api_router.include_router(script.router)
api_router.include_router(progress.router)
