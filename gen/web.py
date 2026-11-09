import os, json, uuid, asyncio, subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask

SOCK = os.environ.get("BLENDER_SOCK", "/dev/shm/blender.sock")

worker = None
lock = asyncio.Lock()  # один Blender обрабатывает один запрос за раз


async def start_worker():
    global worker
    if os.path.exists(SOCK):
        os.remove(SOCK)
    worker = subprocess.Popen([
        "blender",
        "-b", "cube.blend",
        "--factory-startup",
        "--python-exit-code", "1",
        "--python", "worker.py",
    ])
    # Воркер создаёт сокет после прогрева
    for _ in range(1200):
        if os.path.exists(SOCK):
            return
        if worker.poll() is not None:
            raise RuntimeError("blender worker exited during startup")
        await asyncio.sleep(0.1)
    raise RuntimeError("blender worker startup timeout")


def stop_worker():
    if worker and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_worker()
    yield
    stop_worker()


app = FastAPI(lifespan=lifespan)
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


async def call_worker(payload: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(SOCK)
    try:
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise ConnectionError("worker closed connection")
        return json.loads(line)
    finally:
        writer.close()


@app.get("/")
async def generate(x: float = 100.0, y: float = 100.0):
    out = f"/dev/shm/m_{uuid.uuid4().hex}.glb"
    payload = {"x": x, "y": y, "out": out}

    async with lock:
        try:
            if worker is None or worker.poll() is not None:
                raise ConnectionError("worker is not running")
            resp = await call_worker(payload)
        except (ConnectionError, OSError):
            # Воркер упал: перезапускаем и пробуем один раз
            stop_worker()
            try:
                await start_worker()
                resp = await call_worker(payload)
            except Exception as e:
                raise HTTPException(500, f"blender worker failed: {e}")

    if not resp.get("ok"):
        remove_file(out)
        raise HTTPException(500, resp.get("error", "unknown error"))

    return FileResponse(
        path=out,
        filename="m.glb",
        media_type="model/gltf-binary",
        background=BackgroundTask(remove_file, out),
    )
