from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Atlas API is running 🚀"}
    