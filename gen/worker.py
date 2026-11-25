"""Постоянно запущенный Blender-воркер."""
import bpy, os, json, socket, time, traceback, gc, ctypes
from collections import namedtuple

SOCK_PATH = os.environ.get("BLENDER_SOCK", "/dev/shm/blender.sock")
# Искусственная добавка к времени расчёта модели, секунды (0 — без задержки)
EXTRA_DELAY = 0

obj = bpy.data.objects['Basket']
dimensions = bpy.data.node_groups["_dimensions"]

# Реестр параметров сцены: имя параметра → (сокет в группе нод, где лежит значение; минимум; максимум).
# Через один и тот же сокет значение и задаём, и читаем обратно (read_params).
# Пределы — допустимые значения параметра: их проверяет gen и по запросу {"op":"limits"} получает отсюда;
# клиенты (веб) берут их у gen (GET /limits), своих копий нигде нет.
# Новый параметр = одна строка здесь; сигнатуры generate/handle и протокол не меняются.
Param = namedtuple("Param", "socket min max")
PARAMS = {
	"x": Param(lambda: dimensions.nodes["Value"].outputs[0], 20, 256),  # 256 — стол принтера Bambu Lab A1
	"y": Param(lambda: dimensions.nodes["Value.001"].outputs[0], 20, 256),
	"z": Param(lambda: dimensions.nodes["Group Output"].inputs[3], 20, 150),
	"thickness": Param(lambda: dimensions.nodes["Group Output"].inputs[5], 0.4, 10),
	"excavation_height": Param(lambda: dimensions.nodes["Group Output"].inputs[6], 0, 150),
	"perforation": Param(lambda: dimensions.nodes["Group Output"].inputs[7], 10, 100),
}
LIMITS = {name: {"min": p.min, "max": p.max} for name, p in PARAMS.items()}

def apply_params(params):
	"""Записывает параметры в сцену. Неуказанные остаются как были (сцена живёт между запросами)."""
	unknown = params.keys() - PARAMS.keys()
	if unknown:
		raise KeyError(f"неизвестные параметры: {', '.join(sorted(unknown))}")
	for name, value in params.items():
		sock = PARAMS[name].socket()
		# Протокол присылает числа, а тип сокета (float/int/bool) задаёт сцена — приводим к нему
		sock.default_value = type(sock.default_value)(value)


def read_params():
	"""Значения всех параметров, как они сейчас стоят в сцене (в т.ч. не заданные клиентами — из basket.blend)."""
	values = {}
	for name, param in PARAMS.items():
		value = param.socket().default_value
		# Blender хранит float32: 33.3 читается как 33.29999923706055 — округляем до десятитысячных
		values[name] = round(value, 4) if isinstance(value, float) else value
	return values


def generate(params, out):
	"""Применяет params (dict имя → значение), пишет GLB в out; возвращает значения параметров, как они стоят в сцене."""
	apply_params(params)
	values = read_params()
	# Значения поменялись в уже загруженной сцене: помечаем объект и пересчитываем граф
	obj.update_tag()
	bpy.context.view_layer.update()

	depsgraph = bpy.context.evaluated_depsgraph_get()
	eval_obj = obj.evaluated_get(depsgraph)
	mesh_data = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph)
	export_obj = bpy.data.objects.new("ExportTemp", mesh_data)
	try:
		bpy.context.scene.collection.objects.link(export_obj)
		# Без bpy.ops.object.select_all: в фоновом режиме у него падает poll() по контексту
		for o in bpy.context.view_layer.objects:
			o.select_set(False)
		export_obj.select_set(True)
		bpy.context.view_layer.objects.active = export_obj

		bpy.ops.export_scene.gltf(
			filepath=out,
			export_format='GLB',
			use_selection=True
		)
		return values
	finally:
		# Сцена живёт между запросами, поэтому временные данные убираем сразу
		bpy.data.objects.remove(export_obj, do_unlink=True)
		bpy.data.meshes.remove(mesh_data)


def release_memory():
	"""Убирает мусор после расчёта, чтобы память процесса не ползла вверх (лимит Render — 512 МБ).

	Экспортёр glTF плодит много Python-объектов со взаимными ссылками: сборщик мусора убирает их не сразу,
	и пока они лежат, пик растёт на десятки МБ. Затем отдаём системе свободное у аллокаторов
	(Blender загружает аллокатор Intel TBB; 2 = TBBMALLOC_CLEAN_ALL_BUFFERS, 0 не трогать — это «большие страницы»).
	"""
	gc.collect()
	ctypes.CDLL("libtbbmalloc.so.2").scalable_allocation_command(2, None)
	ctypes.CDLL("libc.so.6").malloc_trim(0)


def handle(conn):
	with conn, conn.makefile("rwb") as f:
		line = f.readline()
		if not line:
			return
		try:
			req = json.loads(line)
			if req.get("op") == "limits":
				# Не расчёт: пределы параметров из реестра (gen спрашивает после каждого запуска воркера)
				resp = {"ok": True, "limits": LIMITS}
			else:
				out = req.pop("out")
				values = generate({k: float(v) for k, v in req.items()}, out)
				time.sleep(EXTRA_DELAY)  # имитация долгого расчёта; прогрев её не затрагивает
				resp = {"ok": True, "params": values}
		except Exception as e:
			traceback.print_exc()
			resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
		f.write(json.dumps(resp).encode() + b"\n")
		f.flush()
		release_memory()  # после ответа: клиент уже получил модель и не ждёт очистку


# Прогрев: первый экспорт импортирует glTF-аддон. Делаем это до публикации сокета
generate({"x": 100.0, "y": 100.0, "z": 100.0}, "/dev/shm/m_warmup.glb")
os.remove("/dev/shm/m_warmup.glb")
release_memory()

if os.path.exists(SOCK_PATH):
	os.remove(SOCK_PATH)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK_PATH)  # появление сокета = воркер готов
srv.listen(16)
print("worker ready", flush=True)

while True:
	conn, _ = srv.accept()
	handle(conn)
