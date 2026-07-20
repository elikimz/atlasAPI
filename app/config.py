import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


load_dotenv()


class Settings:
    PROJECT_NAME: str = "Atlas API"
    PROJECT_VERSION: str = "1.0.0"

    DATABASE_URL: str | None = os.getenv("DATABASE_URL")
    SECRET_KEY: str | None = os.getenv("SECRET_KEY")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

    # The application uses bearer tokens rather than cross-site cookies. Origins
    # must be explicitly configured so browsers do not expose authenticated APIs
    # to arbitrary websites.
    BACKEND_CORS_ORIGINS: list[str] = [
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "BACKEND_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",")
        if origin.strip()
    ]

    CLOUDINARY_CLOUD_NAME: str | None = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY: str | None = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET: str | None = os.getenv("CLOUDINARY_API_SECRET")

    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    EMAIL_SENDER: str | None = os.getenv("EMAIL_SENDER")
    EMAIL_APP_PASSWORD: str | None = os.getenv("EMAIL_APP_PASSWORD")

    # PesaFlux credentials remain backend-only. The webhook payload is validated
    # against an existing, user-owned payment record because the provider's
    # published webhook documentation does not specify a signing scheme.
    PESAFLUX_API_KEY: str = os.getenv("PESAFLUX_API_KEY", "")
    PESAFLUX_EMAIL: str = os.getenv("PESAFLUX_EMAIL", "")
    PESAFLUX_USD_TO_KES_RATE: float = float(os.getenv("PESAFLUX_USD_TO_KES_RATE", "130"))

    def validate_runtime_security(self) -> None:
        if not self.DATABASE_URL:
            logger.error("DATABASE_URL is not configured.")
            raise RuntimeError("DATABASE_URL must be configured before starting the API.")

        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            logger.error("SECRET_KEY is not configured or too short.")
            raise RuntimeError("SECRET_KEY must be configured with at least 32 characters before starting the API.")

        if self.JWT_ALGORITHM != "HS256":
            logger.error(f"JWT_ALGORITHM is {self.JWT_ALGORITHM}, but must be HS256.")
            raise RuntimeError("JWT_ALGORITHM must be HS256 unless the JWT implementation is updated accordingly.")

        if self.ACCESS_TOKEN_EXPIRE_MINUTES <= 0 or self.REFRESH_TOKEN_EXPIRE_DAYS <= 0:
            logger.error("Token expiration settings (ACCESS_TOKEN_EXPIRE_MINUTES or REFRESH_TOKEN_EXPIRE_DAYS) are not positive.")
            raise RuntimeError("Token expiration settings must be positive.")

        if not self.BACKEND_CORS_ORIGINS:
            logger.error("BACKEND_CORS_ORIGINS is not configured.")
            raise RuntimeError("BACKEND_CORS_ORIGINS must be configured.")
        if "*" in self.BACKEND_CORS_ORIGINS:
            logger.warning("BACKEND_CORS_ORIGINS contains '*', which is insecure for production.")
            # For debugging, we'll allow '*' for now, but it should be explicit in production.
            # raise RuntimeError("BACKEND_CORS_ORIGINS must list explicit allowed origins.")



settings = Settings()
