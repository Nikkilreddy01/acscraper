from uvicorn import run

if __name__ == "__main__":
    run("src.api.main:app", host="0.0.0.0", port=int(__import__("os").environ.get("PORT", 8000)), reload=False)