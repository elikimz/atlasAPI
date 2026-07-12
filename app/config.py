import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "Adpulse API"
    PROJECT_VERSION: str = "1.0.0"

    DATABASE_URL = os.getenv("DATABASE_URL")
    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 43200))

    # CORS settings
    BACKEND_CORS_ORIGINS = os.getenv("BACKEND_CORS_ORIGINS", "*").split(",")

    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
    
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    EMAIL_SENDER = os.getenv("EMAIL_SENDER", "adpulseai2@gmail.com")
    EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "xxjhvcdmhzeoekgj")

    if "*" in BACKEND_CORS_ORIGINS:
        BACKEND_CORS_ORIGINS = ["*"]

    # ── PesaFlux M-Pesa STK Push (NEW — additive only) ──────────────────────
    # These credentials are BACKEND-ONLY. Never expose to frontend.
    PESAFLUX_API_KEY: str = os.getenv("PESAFLUX_API_KEY", "")
    PESAFLUX_EMAIL: str = os.getenv("PESAFLUX_EMAIL", "")
    # Conversion rate: 1 USD = N KES. Override in production env as needed.
    PESAFLUX_USD_TO_KES_RATE: float = float(os.getenv("PESAFLUX_USD_TO_KES_RATE", "130"))

settings = Settings()
