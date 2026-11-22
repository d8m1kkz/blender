import os, re, json, uuid, asyncio, subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

SOCK = os.environ.get("BLENDER_SOCK", "/dev/shm/blender.sock")

worker = None

# ── Общая корзина: одно состояние на всех клиентов (живёт в памяти процесса) ──
# state — желаемые параметры: последняя правка любого клиента; version растёт с каждой правкой.
# Стартуем с version=1 и дефолтами клиента, чтобы модель была готова к первому подключению.
state = {"version": 1, "x": 100.0, "y": 100.0, "z": 50.0}
# Параметры модели, которые клиенты могут менять (имена — как в PARAMS у gen/worker.py).
# Правка может прийти частичной; что ещё никто не задавал, в state нет — Blender берёт своё из basket.blend.
PARAM_KEYS = ("x", "y", "z", "thickness", "excavation_height", "perforation_wall", "perforation_floor")
# view — общий «вид»: цвет, камера, ориентация модели, свет. Blender не нужен, поэтому правка
# рассылается сразу; свой version, независимый от version корзины. Дефолты совпадают с клиентом.
VIEW_DEFAULTS = {
	"color": "#29a546",          # цвет пластика модели
	"distance": 1300.0,          # расстояние от камеры до центра стола, мм (модель 100×100 целиком влезает и в узкий экран телефона)
	"rot_x": 0.0, "rot_y": 0.0, "rot_z": 0.0,  # ориентация модели, градусы вокруг осей сцены
	"light_color": "#fff0dd",    # цвет направленного света
	"light_brightness": 1.0,     # множитель яркости всего освещения (1 — как по умолчанию)
	"spin": -3.44,               # автовращение стола с моделью, градусы в секунду (0 — стоит)
}
view = {"version": 1, **VIEW_DEFAULTS}
VIEW_RANGES = {
	"distance": (50, 5000),
	"rot_x": (-360, 360), "rot_y": (-360, 360), "rot_z": (-360, 360),
	"light_brightness": (0, 10),
	"spin": (-360, 360),
}
VIEW_COLORS = ("color", "light_color")
ready = None       # последняя опубликованная (актуальная) модель: {version, <PARAM_KEYS из Blender>, stats, glb}
last_error = None  # последняя ошибка актуальной версии: {version, error}
done_version = 0   # последняя обработанная циклом версия (опубликованная, выброшенная или с ошибкой)
# Будит цикл генерации и отправителей всех соединений при любом изменении
changed = asyncio.Condition()


async def start_worker():
	global worker
	if os.path.exists(SOCK):
		os.remove(SOCK)
	worker = subprocess.Popen([
		"blender",
		"-b", "basket.blend",
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


async def render(snap: dict) -> tuple[bytes, dict, dict]:
	"""Генерирует модель по снимку параметров: GLB байтами, статистика сетки и значения параметров из Blender."""
	out = f"/dev/shm/m_{uuid.uuid4().hex}.glb"
	# Шлём все заданные параметры, а не только изменённые: перезапущенный воркер начинает со сцены из файла
	payload = {k: v for k, v in snap.items() if k in PARAM_KEYS} | {"out": out}
	try:
		try:
			if worker is None or worker.poll() is not None:
				raise ConnectionError("worker is not running")
			resp = await call_worker(payload)
		except (ConnectionError, OSError):
			# Воркер упал: перезапускаем и пробуем один раз
			stop_worker()
			await start_worker()
			resp = await call_worker(payload)
		if not resp.get("ok"):
			raise RuntimeError(resp.get("error", "unknown error"))
		with open(out, "rb") as f:
			return f.read(), resp.get("stats", {}), resp.get("params", {})
	finally:
		remove_file(out)


async def generation_loop():
	"""Единственный, кто разговаривает с Blender: всегда считает самую свежую версию."""
	global ready, last_error, done_version
	while True:
		async with changed:
			await changed.wait_for(lambda: state["version"] > done_version)
			snap = dict(state)

		# Blender считает до конца, не прерываем; правки тем временем копятся в state
		try:
			(glb, stats, values), error = await render(snap), None
		except Exception as e:
			glb = stats = values = None
			error = f"{type(e).__name__}: {e}"

		async with changed:
			done_version = snap["version"]
			if snap["version"] != state["version"]:
				# Пока считали, пришла новая правка: результат никому не нужен
				print(f"v{snap['version']} устарела (уже v{state['version']}), не отправляем", flush=True)
				continue
			if error is None:
				# Значения — те, что реально стоят в сцене Blender (поверх запрошенных)
				ready = {**snap, **values, "stats": stats, "glb": glb}
				print(f"v{snap['version']} готова, рассылаем", flush=True)
			else:
				last_error = {"version": snap["version"], "error": error}
				print(f"v{snap['version']} ошибка: {error}", flush=True)
			changed.notify_all()


@asynccontextmanager
async def lifespan(app: FastAPI):
	await start_worker()
	loop_task = asyncio.create_task(generation_loop())
	yield
	loop_task.cancel()
	stop_worker()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


def parse_view(msg: dict) -> dict:
	"""Частичная правка вида: проверяет поля, ValueError — если что-то неверно или полей нет.

	reset: true — вернуть всё к умолчаниям (остальные поля сообщения тогда не смотрим).
	"""
	if msg.get("reset") is True:
		return dict(VIEW_DEFAULTS)
	patch = {}
	for key in VIEW_COLORS:
		if key in msg:
			value = msg[key]
			if not (isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
				raise ValueError(f"{key}: ожидается #RRGGBB")
			patch[key] = value.lower()
	for key, (lo, hi) in VIEW_RANGES.items():
		if key in msg:
			value = float(msg[key])
			if not lo <= value <= hi:  # заодно отсекает NaN
				raise ValueError(f"{key}: вне диапазона {lo}…{hi}")
			patch[key] = value
	if not patch:
		raise ValueError("нет полей вида")
	return patch


async def send_loop(ws: WebSocket, conn: dict):
	"""Единственный, кто пишет в это соединение: порядок «model → бинарный кадр» не нарушится."""
	sent_ack = 0
	sent_model = 0
	sent_view = 0
	# Старые ошибки новому клиенту не показываем — только те, что случатся при нём
	sent_error = last_error["version"] if last_error else 0

	def has_news():
		return (
			conn["ack"] > sent_ack
			or view["version"] > sent_view
			or (ready is not None and ready["version"] > sent_model)
			or (last_error is not None and last_error["version"] > sent_error)
		)

	try:
		while True:
			# Под блокировкой только снимок; отправляем вне её, чтобы медленный клиент не тормозил остальных.
			# Если клиент не успевает, промежуточные модели он просто пропустит — получит самую свежую.
			async with changed:
				await changed.wait_for(has_news)
				ack, seq, model, error = conn["ack"], conn["seq"], ready, last_error
				view_snap = dict(view)

			if view_snap["version"] > sent_view:
				await ws.send_json({"type": "view", **view_snap})
				sent_view = view_snap["version"]

			# Несколько быстрых правок могут получить один ack: seq — номер последней из них у клиента
			if ack > sent_ack:
				await ws.send_json({"type": "ack", "version": ack, "seq": seq})
				sent_ack = ack
			if model is not None and model["version"] > sent_model:
				await ws.send_json({
					"type": "model",
					"version": model["version"],
					**{k: model[k] for k in PARAM_KEYS if k in model},
					"stats": model["stats"],
					"glb_bytes": len(model["glb"]),
				})
				if conn["glb"]:
					await ws.send_bytes(model["glb"])
				sent_model = model["version"]
			if error is not None and error["version"] > sent_error:
				await ws.send_json({"type": "error", **error})
				sent_error = error["version"]
	except Exception:
		# Соединение закрылось посреди отправки — приёмник в ws_endpoint это тоже увидит
		pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
	await ws.accept()
	# ack — последняя версия, присвоенная правке этого клиента; seq — номер этой правки у клиента;
	# glb — слать ли бинарный кадр с моделью (/ws?glb=0 — только заголовки, например для MCP)
	conn = {"ack": 0, "seq": 0, "glb": ws.query_params.get("glb") != "0"}
	sender = asyncio.create_task(send_loop(ws, conn))
	try:
		while True:
			try:
				msg = json.loads(await ws.receive_text())
				kind = msg.get("type")
				if kind == "view":
					patch = parse_view(msg)
				elif kind == "params":
					# Частичная правка: неуказанные параметры остаются прежними
					patch = {k: float(msg[k]) for k in PARAM_KEYS if k in msg}
					if not patch:
						raise ValueError("нет параметров")
					seq = int(msg.get("seq", 0))
				else:
					continue
			except (ValueError, KeyError, TypeError, AttributeError):
				continue  # битое сообщение пропускаем, соединение не рвём
			if kind == "view":
				async with changed:
					view.update(patch)
					view["version"] += 1
					changed.notify_all()
				continue
			async with changed:
				state["version"] += 1
				state.update(patch)
				conn["ack"], conn["seq"] = state["version"], seq
				changed.notify_all()
	except WebSocketDisconnect:
		pass
	finally:
		sender.cancel()


@app.get("/model")
async def model():
	"""Текущая опубликованная модель: через nginx — /model.glb (ссылку выдаёт MCP), в dev ещё :8080/model.

	Отдаём ту, что актуальна в момент скачивания: размеры и версия — в имени файла.
	"""
	if ready is None:
		raise HTTPException(503, "модель ещё не готова")
	name = f"basket_v{ready['version']}_{ready['x']:g}x{ready['y']:g}x{ready['z']:g}.glb"
	return Response(
		content=ready["glb"],
		media_type="model/gltf-binary",
		headers={
			"X-Version": str(ready["version"]),
			"Content-Disposition": f'attachment; filename="{name}"',
			"Cache-Control": "no-store",  # по тому же адресу после правки будет другая модель
		},
	)
