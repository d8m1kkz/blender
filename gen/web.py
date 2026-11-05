import os, json, subprocess
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def remove_file(path: str):
    if os.path.exists(path):
        os.remove(path)

@app.get("/")
def generate(x: float = 100.0, y: float = 100.0):
    subprocess.run([
        "blender",
        "-b", "cube.blend",
        "--factory-startup",
        "--python-exit-code", "1",
        "--python", "generate.py",
        "--",
        str(x),
        str(y)
    ])

    return FileResponse(
        path="/dev/shm/m.glb",
        filename=f"m.glb",
        media_type="model/gltf-binary"
    )