from fastapi import FastAPI
from app.routers import auth, core, extra
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings



app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(core.router)
app.include_router(extra.router)


@app.get("/")
def root():
    return {"message": "Adpulse API is running 🚀"}
    