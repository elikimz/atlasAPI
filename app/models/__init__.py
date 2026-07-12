# Import all models so that Base.metadata.create_all() discovers them.
# Existing models are unchanged. PesaFluxPayment is a new addition.
from app.models import models  # noqa: F401 — existing models
from app.models.pesaflux_payment import PesaFluxPayment  # noqa: F401 — new PesaFlux model
