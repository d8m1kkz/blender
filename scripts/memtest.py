"""Замер памяти контейнера gen в разных режимах. Запускается ВНУТРИ контейнера: scripts/measure_mem.sh.

Память берётся из cgroup — по ней платформа (Render, Docker --memory) решает, убивать ли контейнер:
в неё входят процессы, файловый кэш и /dev/shm. Пик ловим частым опросом на время каждого режима.
"""
import asyncio, json, os, sys, time

CG = "/sys/fs/cgroup"
PORT = os.environ.get("PORT", "8080")
URL = f"ws://127.0.0.1:{PORT}/ws"


def read(name):
	try:
		return open(f"{CG}/{name}").read().strip()
	except OSError:
		return None


def stat():
	return {k: int(v) for k, v in (l.split() for l in read("memory.stat").splitlines())}


def mb(n):
	return n / 1048576


def rss_by_process():
	"""RSS процессов контейнера (МБ): отдельно Blender и uvicorn."""
	out = {}
	for pid in filter(str.isdigit, os.listdir("/proc")):
		try:
			comm = open(f"/proc/{pid}/comm").read().strip()
			for line in open(f"/proc/{pid}/status"):
				if line.startswith("VmRSS:"):
					out[comm] = out.get(comm, 0) + int(line.split()[1]) / 1024
		except OSError:
			pass
	return out


class Sampler:
	"""Опрашивает memory.current каждые 50 мс и запоминает максимум."""
	def __init__(self):
		self.peak = 0
		self._task = None

	async def _run(self):
		while True:
			self.peak = max(self.peak, int(read("memory.current")))
			await asyncio.sleep(0.05)

	def start(self):
		self.peak = int(read("memory.current"))
		self._task = asyncio.create_task(self._run())

	def stop(self):
		self._task.cancel()
		self.peak = max(self.peak, int(read("memory.current")))


async def connect():
	import websockets
	return await websockets.connect(URL, max_size=None)


async def wait_model(ws, min_version=0):
	"""Ждёт заголовок model версии ≥ min_version и возвращает его (бинарные кадры пропускает)."""
	while True:
		m = await ws.recv()
		if isinstance(m, bytes):
			continue
		msg = json.loads(m)
		if msg.get("type") == "error":
			raise RuntimeError(msg.get("error"))
		if msg.get("type") == "model" and msg["version"] >= min_version:
			return msg


async def set_and_wait(ws, params, seq=[0]):
	seq[0] += 1
	await ws.send(json.dumps({"type": "params", "seq": seq[0], **params}))
	ack = None
	while True:
		m = await ws.recv()
		if isinstance(m, bytes):
			continue
		msg = json.loads(m)
		if msg.get("type") == "error":
			raise RuntimeError(msg.get("error"))
		if msg.get("type") == "ack" and msg["seq"] == seq[0]:
			ack = msg["version"]
		if msg.get("type") == "model" and ack and msg["version"] >= ack:
			return msg


results = []


async def mode(name, coro):
	"""Один режим: до/после и пик. coro — действие, нагружающее gen."""
	before = int(read("memory.current"))
	s = Sampler()
	s.start()
	t0 = time.time()
	try:
		note = await coro
	except Exception as e:
		note = f"ОШИБКА: {e!r}"
	dt = time.time() - t0
	await asyncio.sleep(1.0)  # дать памяти «осесть»
	s.stop()
	after = int(read("memory.current"))
	st = stat()
	rss = rss_by_process()
	results.append((name, mb(before), mb(s.peak), mb(after), dt))
	print(f"\n── {name}")
	print(f"   до {mb(before):.0f} МБ, пик {mb(s.peak):.0f} МБ, после {mb(after):.0f} МБ, время {dt:.1f} с" + (f" · {note}" if note else ""))
	print(f"   cgroup: anon {mb(st['anon']):.0f}, file(кэш) {mb(st['file']):.0f}, shmem(/dev/shm) {mb(st['shmem']):.0f} МБ"
	      f" · RSS: " + ", ".join(f"{k} {v:.0f}" for k, v in sorted(rss.items(), key=lambda kv: -kv[1])[:3]))


async def main():
	limit = read("memory.max")
	print(f"Лимит cgroup: {'нет' if limit == 'max' else f'{mb(int(limit)):.0f} МБ'}")
	ws = None
	for _ in range(600):  # ждём прогрева Blender (первая модель появляется только после него)
		try:
			ws = await connect()
			break
		except OSError:
			await asyncio.sleep(0.5)
	first = await wait_model(ws)
	print(f"Первая модель: версия {first['version']}, x={first['x']} y={first['y']} z={first['z']}, GLB {first['glb_bytes']/1024:.0f} КБ")
	base = {k: first[k] for k in ("thickness", "excavation_height", "perforation") if k in first}

	await mode("1. Покой после прогрева (первая модель уже посчитана)", asyncio.sleep(0))

	async def one(params):
		m = await set_and_wait(ws, params)
		return f"GLB {m['glb_bytes']/1024:.0f} КБ"

	await mode("2. Минимальная корзина 20×20×20", one({"x": 20, "y": 20, "z": 20}))
	await mode("3. Типичная 100×100×50", one({"x": 100, "y": 100, "z": 50}))
	await mode("4. Максимальная 256×256×150", one({"x": 256, "y": 256, "z": 150}))

	# Самая тяжёлая геометрия: максимальный размер + втрое более частая решётка и тонкие прутки
	if base:
		dense = {"x": 256, "y": 256, "z": 150,
		         "thickness": base["thickness"] * 0.5,
		         "perforation": base["perforation"] * 0.33}
		await mode(f"5. Максимум + густая решётка {dense}", one(dense))

	async def slider():
		"""Имитация перетаскивания слайдера: 40 правок подряд с шагом 30 мс."""
		for i in range(40):
			await ws.send(json.dumps({"type": "params", "seq": 1000 + i, "x": 60 + i * 4, "y": 60 + i * 4, "z": 40}))
			await asyncio.sleep(0.03)
		await wait_model(ws, 0)
		await asyncio.sleep(3)
		return "40 правок"
	await mode("6. Перетаскивание слайдера (40 правок подряд)", slider())

	async def repeat():
		for i in range(30):
			await set_and_wait(ws, {"x": 80 + (i % 2) * 100, "y": 120, "z": 60})
		return "30 пересчётов"
	await mode("7. Утечки: 30 пересчётов подряд", repeat())

	async def clients():
		extra = [await connect() for _ in range(5)]
		await set_and_wait(ws, {"x": 200, "y": 150, "z": 100})
		for c in extra:
			await wait_model(c)
		for c in extra:
			await c.close()
		return "5 дополнительных клиентов"
	await mode("8. Шесть клиентов получают модель", clients())

	print("\n══ Итог")
	print(f"{'режим':62} {'до':>6} {'пик':>6} {'после':>6}")
	for n, b, p, a, _ in results:
		print(f"{n[:62]:62} {b:6.0f} {p:6.0f} {a:6.0f}")
	peak = read("memory.peak")
	if peak:
		print(f"\nmemory.peak за всё время жизни контейнера: {mb(int(peak)):.0f} МБ (лимит Render free — 512 МБ)")
	print(f"memory.events: {read('memory.events').replace(chr(10), ', ')}  (oom_kill > 0 — контейнер убивали)")


asyncio.run(main())
