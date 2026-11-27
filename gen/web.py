import os, re, json, uuid, asyncio, subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

SOCK = os.environ.get("BLENDER_SOCK", "/dev/shm/blender.sock")

worker = None

# ── Общая корзина: одно состояние на всех клиентов (живёт в памяти процесса) ──
# state — желаемые параметры: последняя правка любого клиента; version растёт с каждой правкой.
# Стартуем с version=1 и размерами по умолчанию, чтобы модель была готова к первому подключению.
state = {"version": 1, "x": 100.0, "y": 100.0, "z": 50.0}
# Правка может прийти частичной; что ещё никто не задавал, в state нет — Blender берёт своё из basket.blend.
# view — общий «вид»: цвет, камера, ориентация модели, свет. Правка
# рассылается сразу; свой version, независимый от version корзины.
VIEW_DEFAULTS = {
	"color": "#29a546",          # цвет пластика модели
	"distance": 1300.0,          # расстояние от камеры до центра стола, мм (модель 100×100 целиком влезает и в узкий экран телефона)
	"rot_x": 0.0, "rot_y": 0.0, "rot_z": 0.0,  # ориентация модели, градусы вокруг осей сцены
	"light_color": "#fff0dd",    # цвет направленного света
	"light_brightness": 1.0,     # множитель яркости всего освещения (1 — как по умолчанию)
	"spin": -3.44,               # автовращение стола с моделью, градусы в секунду (0 — стоит)
}

# distance_version — version правки, в которой расстояние задавали последний раз. Пользователь меняет масштаб
# у себя в браузере, поэтому клиент берёт distance, только когда это число изменилось.
view = {"version": 1, "distance_version": 1, **VIEW_DEFAULTS}
VIEW_RANGES = {
	"distance": (50, 5000),
	"rot_x": (-360, 360), "rot_y": (-360, 360), "rot_z": (-360, 360),
	"light_brightness": (0, 10),
	"spin": (-360, 360),
}
VIEW_COLORS = ("color", "light_color")
# Сколько секунд ждём, пока клиент примет одно сообщение; дольше — отключаем (зависший клиент не должен держать сервер)
SEND_TIMEOUT = float(os.environ.get("SEND_TIMEOUT", "20"))
# Параметры модели {имя: {"min", "max"}}: задаются в PARAMS у worker.py, сюда приходят после каждого
# запуска воркера (load_limits). Ключи — это и есть список параметров.
# Правки вне диапазона отбрасываются; веб-клиент получает пределы в сообщении «model», остальные — через GET /limits.
limits: dict = {}
ready = None       # последняя опубликованная (актуальная) модель: {version, <параметры из Blender>, glb}
last_error = None  # последняя ошибка актуальной версии: {version, error}
done_version = 0   # последняя обработанная циклом версия (опубликованная, выброшенная или с ошибкой)
# Будит цикл генерации и отправителей всех соединений при любом изменении
changed = asyncio.Condition()


def pick_params(source: dict) -> dict:
	"""Только параметры модели из source; отсутствующие пропускаются."""
	return {k: source[k] for k in limits if k in source}


def check_range(key: str, value, lo: float, hi: float) -> float:
	"""Приводит value к числу и проверяет пределы; ValueError — если не число или вне диапазона."""
	value = float(value)
	if not lo <= value <= hi:  # заодно отсекает NaN
		raise ValueError(f"{key}: вне диапазона {lo}…{hi}")
	return value


async def start_worker():
	global worker
	remove_file(SOCK)
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
			await load_limits()
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


async def load_limits():
	"""Спрашивает у воркера пределы параметров (сокет появляется чуть раньше listen — пробуем несколько раз)."""
	global limits
	for _ in range(50):
		try:
			resp = await call_worker({"op": "limits"})
			break
		except (ConnectionError, OSError):
			await asyncio.sleep(0.1)
	else:
		raise RuntimeError("worker did not answer the limits request")
	limits = resp["limits"]


async def render(snap: dict) -> tuple[bytes, dict]:
	"""Генерирует модель по снимку параметров: GLB байтами и значения параметров из Blender."""
	out = f"/dev/shm/m_{uuid.uuid4().hex}.glb"
	# Шлём все заданные параметры, а не только изменённые: перезапущенный воркер начинает со сцены из файла
	payload = pick_params(snap) | {"out": out}
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
			return f.read(), resp.get("params", {})
	finally:
		remove_file(out)


async def generation_loop():
	"""Единственный, кто разговаривает с Blender: всегда считает самую свежую версию."""
	global ready, last_error, done_version
	while True:
		async with changed:
			await changed.wait_for(lambda: state["version"] > done_version)
			snap = dict(state)

		# Запоминаем параметры, с которыми работает Blender: к ним привяжется наименьший запас памяти.
		# Базой служат значения последней готовой модели (в ней есть и то, что клиенты не задавали).
		blender_params.clear()
		blender_params.update(pick_params({**(ready or {}), **snap}))

		# Пока Blender считает, правки копятся в state
		try:
			(glb, values), error = await render(snap), None
		except Exception as e:
			glb = values = None
			error = f"{type(e).__name__}: {e}"

		async with changed:
			done_version = snap["version"]
			if snap["version"] != state["version"]:
				# Пока считали, пришла новая правка: результат никому не нужен
				print(f"v{snap['version']} устарела (уже v{state['version']}), не отправляем", flush=True)
				continue
			if error is None:
				# Значения — те, что реально стоят в сцене Blender (поверх запрошенных)
				ready = {**snap, **values, "glb": glb}
				print(f"v{snap['version']} готова, рассылаем", flush=True)
			else:
				last_error = {"version": snap["version"], "error": error}
				print(f"v{snap['version']} ошибка: {error}", flush=True)
			changed.notify_all()


# ── Замер запаса памяти (cgroup v2) ──
# Фоновая задача ищет наименьший запас памяти и запоминает строкой: запас и параметры корзины.
# Занятой считаем только память, которую нельзя освободить: процессы, /dev/shm и нужды ядра. Файловый кэш не считаем:
# ядро заполняет им свободное место и освобождает по требованию, поэтому memory.peak доходит до лимита и без нехватки.
# Всплески между замерами не видны, поэтому замеряем часто.
blender_params: dict = {}  # параметры корзины, с которыми сейчас работает (или только что работал) Blender
# Начальный запас заведомо больше любого настоящего: первый же замер станет «минимумом»
mem_min = {"free": 99999999, "line": "нет данных"}


def read_cgroup(name: str) -> str:
	with open(f"/sys/fs/cgroup/{name}") as f:
		return f.read().strip()


def read_used_mb() -> float:
	"""Занятая сейчас память контейнера без файлового кэша, МБ."""
	stat = dict(line.split() for line in read_cgroup("memory.stat").splitlines())
	# shmem — это /dev/shm: в статистике он входит и в файловый кэш (file), но освободить его нельзя
	return sum(int(stat[key]) for key in ("anon", "shmem", "kernel")) / 1048576


# Лимит не меняется, читаем один раз. «max» — без лимита (как локально): тогда берём 512
_max = read_cgroup("memory.max")
MEM_LIMIT = 512 if _max == "max" else int(_max) / 1048576


def sample_memory():
	free = MEM_LIMIT - read_used_mb()
	if free < mem_min["free"]:
		params = " ".join(f"{k}={v:g}" for k, v in blender_params.items()) or "старт, до первой генерации"
		mem_min["free"] = free
		mem_min["line"] = f"RAM мин. запас {free:.1f} МБ: {params}"
		print(mem_min["line"], flush=True)  # в лог: после перезапуска значение в памяти процесса пропадает


async def memory_sampler(interval: float):
	while True:
		sample_memory()
		await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
	await start_worker()
	loop_task = asyncio.create_task(generation_loop())
	# Фоновый замер запаса памяти; MEM_SAMPLE_INTERVAL — период в секундах (0 — не замерять)
	interval = float(os.environ.get("MEM_SAMPLE_INTERVAL", "0.1"))
	mem_task = asyncio.create_task(memory_sampler(interval)) if interval > 0 else None
	yield
	loop_task.cancel()
	if mem_task:
		mem_task.cancel()
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

	reset: true — вернуть всё к умолчаниям.
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
			patch[key] = check_range(key, msg[key], lo, hi)
	if not patch:
		raise ValueError("нет полей вида")
	return patch


def parse_params(msg: dict) -> dict:
	"""Частичная правка параметров модели: ValueError — если параметров нет или значение вне пределов."""
	patch = {k: check_range(k, v, limits[k]["min"], limits[k]["max"]) for k, v in pick_params(msg).items()}
	if not patch:
		raise ValueError("нет параметров")
	return patch


def update_view(patch: dict):
	"""Вносит проверенную правку в общий вид. Вызывать под блокировкой changed."""
	view.update(patch)
	view["version"] += 1
	if "distance" in patch:  # даже если значение прежнее: его задали заново
		view["distance_version"] = view["version"]


async def send_loop(ws: WebSocket, conn: dict):
	"""Единственный, кто пишет в это соединение: порядок «model → бинарный кадр» не нарушится."""
	sent_ack = 0
	sent_model = 0
	sent_view = 0
	# Старые ошибки новому клиенту не показываем — только те, что случатся при нём.
	# Но если ни одной модели ещё нет, ошибку отправляем: иначе клиент будет ждать молча
	sent_error = last_error["version"] if last_error and ready else 0

	async def send(coro):
		await asyncio.wait_for(coro, SEND_TIMEOUT)

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
				await send(ws.send_json({"type": "view", **view_snap}))
				sent_view = view_snap["version"]

			# Несколько быстрых правок могут получить один ack: seq — номер последней из них у клиента
			if ack > sent_ack:
				await send(ws.send_json({"type": "ack", "version": ack, "seq": seq}))
				sent_ack = ack
			if model is not None and model["version"] > sent_model:
				await send(ws.send_json({
					"type": "model",
					"version": model["version"],
					**pick_params(model),
					"limits": limits,
					"glb_bytes": len(model["glb"]),
				}))
				await send(ws.send_bytes(model["glb"]))
				sent_model = model["version"]
			if error is not None and error["version"] > sent_error:
				await send(ws.send_json({"type": "error", **error}))
				sent_error = error["version"]
	except asyncio.TimeoutError:
		# Клиент не читает данные: не держим его, иначе зависшая отправка копит память и тормозит сервер
		print(f"клиент не принял данные за {SEND_TIMEOUT:g} с, отключаем", flush=True)
		try:
			await asyncio.wait_for(ws.close(), 5)
		except Exception:
			pass
	except Exception:
		# Соединение закрылось посреди отправки — приёмник в ws_endpoint это тоже увидит
		pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
	await ws.accept()
	# ack — последняя версия, присвоенная правке этого клиента; seq — номер этой правки у клиента
	conn = {"ack": 0, "seq": 0}
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
					patch = parse_params(msg)
					seq = int(msg.get("seq", 0))
				else:
					continue
			except (ValueError, KeyError, TypeError, AttributeError):
				continue  # битое сообщение пропускаем
			async with changed:
				if kind == "view":
					update_view(patch)
				else:
					state.update(patch)
					state["version"] += 1
					conn["ack"], conn["seq"] = state["version"], seq
				changed.notify_all()
	except WebSocketDisconnect:
		pass
	finally:
		sender.cancel()


@app.get("/limits")
async def get_limits():
	"""Допустимые значения параметров модели {имя: {min, max}} — из реестра PARAMS у Blender-воркера."""
	if not limits:
		raise HTTPException(503, "пределы ещё не получены")
	return limits


# Сколько секунд POST /params ждёт готовую модель; меньше таймаута клиента (PHP ждёт 25 с)
PARAMS_WAIT = float(os.environ.get("PARAMS_WAIT", "20"))


@app.get("/params")
async def get_params():
	"""Текущие значения параметров модели — у последней готовой модели."""
	if ready is None:
		raise HTTPException(503, "модель ещё не готова")
	return {"version": ready["version"], **pick_params(ready)}


@app.post("/params")
async def post_params(patch: dict):
	"""Частичная правка параметров (как сообщение params по WebSocket); ждёт готовую модель.

	Правка попадает в общую корзину: веб-клиенты получат новую модель как обычно.
	Ответ — параметры последней рассчитанной модели (если следом пришла чужая правка, это будет
	более новая модель) или ошибка: 500 — расчёт не удался, 504 — модель не готова за PARAMS_WAIT.
	"""
	try:
		patch = parse_params(patch)
	except (ValueError, TypeError) as e:
		raise HTTPException(422, str(e))
	async with changed:
		state.update(patch)
		state["version"] += 1
		version = state["version"]
		changed.notify_all()
		try:
			# Ждём результат (модель или ошибку) нашей или более новой версии:
			# цикл генерации мог пропустить нашу, если следом пришла ещё правка
			await asyncio.wait_for(changed.wait_for(lambda: max(
				ready["version"] if ready else 0,
				last_error["version"] if last_error else 0,
			) >= version), PARAMS_WAIT)
		except asyncio.TimeoutError:
			raise HTTPException(504, f"модель не рассчитана за {PARAMS_WAIT:g} с")
		# Итог — самый свежий результат: если последней была ошибка, сообщаем её
		if last_error and last_error["version"] >= version and (ready is None or last_error["version"] > ready["version"]):
			raise HTTPException(500, last_error["error"])
		return {"version": ready["version"], **pick_params(ready)}


@app.get("/view/limits")
async def get_view_limits():
	"""Допустимые значения числовых полей общего вида {имя: {min, max}} (VIEW_RANGES)."""
	return {k: {"min": lo, "max": hi} for k, (lo, hi) in VIEW_RANGES.items()}


@app.get("/view")
async def get_view():
	"""Текущий общий вид {version, color, distance, spin, …}."""
	return dict(view)


@app.post("/view")
async def post_view(patch: dict):
	"""Частичная правка общего вида (как сообщение view по WebSocket):
	правка сразу рассылается всем клиентам, ответ — общий вид после неё."""
	try:
		patch = parse_view(patch)
	except (ValueError, TypeError) as e:
		raise HTTPException(422, str(e))
	async with changed:
		update_view(patch)
		changed.notify_all()
		return dict(view)


@app.get("/memory")
async def memory():
	"""Одна строка: наименьший запас памяти с запуска и параметры корзины в тот момент."""
	return PlainTextResponse(mem_min["line"])
