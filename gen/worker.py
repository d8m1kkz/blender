"""Постоянно запущенный Blender-воркер."""
import bpy, bmesh, os, json, socket, time, traceback

SOCK_PATH = os.environ.get("BLENDER_SOCK", "/dev/shm/blender.sock")
# Искусственная добавка к времени расчёта модели, секунды (0 — без задержки)
EXTRA_DELAY = 0

obj = bpy.data.objects['Basket']
dimensions = bpy.data.node_groups["_dimensions"];

def mesh_stats(mesh):
	"""Объём, площадь и габариты сетки (единицы сцены = мм). Объём честный только у замкнутой сетки.

	Geometry Nodes собирают корзину из кусков, стыкующихся совпадающими вершинами: склеиваем их
	(только в копии для подсчёта, экспорт не трогаем), иначе каждый стык считался бы открытым краем.
	"""
	bm = bmesh.new()
	try:
		bm.from_mesh(mesh)
		bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
		coords = [v.co for v in bm.verts]
		return {
			"volume_mm3": abs(bm.calc_volume()),
			"area_mm2": sum(f.calc_area() for f in bm.faces),
			"size_mm": [max(c[i] for c in coords) - min(c[i] for c in coords) for i in range(3)],
			"open_edges": sum(1 for e in bm.edges if not e.is_manifold),
		}
	finally:
		bm.free()


# Реестр параметров сцены: имя параметра → сокет в группе нод, где лежит его значение.
# Через один и тот же сокет значение и задаём, и читаем обратно (read_params).
# Новый параметр = одна строка здесь; сигнатуры generate/handle и протокол не меняются.
PARAMS = {
	"x": lambda: dimensions.nodes["Value"].outputs[0],
	"y": lambda: dimensions.nodes["Value.001"].outputs[0],
	"z": lambda: dimensions.nodes["Group Output"].inputs[3],
	"thickness": lambda: dimensions.nodes["Group Output"].inputs[5],
	"excavation_height": lambda: dimensions.nodes["Group Output"].inputs[6],
	"perforation_wall": lambda: dimensions.nodes["Group Output"].inputs[7],
	"perforation_floor": lambda: dimensions.nodes["Group Output"].inputs[8],
}

def apply_params(params):
	"""Записывает параметры в сцену. Неуказанные остаются как были (сцена живёт между запросами)."""
	unknown = params.keys() - PARAMS.keys()
	if unknown:
		raise KeyError(f"неизвестные параметры: {', '.join(sorted(unknown))}")
	for name, value in params.items():
		sock = PARAMS[name]()
		# Протокол присылает числа, а тип сокета (float/int/bool) задаёт сцена — приводим к нему
		sock.default_value = type(sock.default_value)(value)


def read_params():
	"""Значения всех параметров, как они сейчас стоят в сцене (в т.ч. не заданные клиентами — из basket.blend)."""
	values = {}
	for name, socket in PARAMS.items():
		value = socket().default_value
		# Blender хранит float32: 33.3 читается как 33.29999923706055 — округляем до десятитысячных
		values[name] = round(value, 4) if isinstance(value, float) else value
	return values


def generate(params, out):
	"""Применяет params (dict имя → значение), пишет GLB в out; возвращает статистику сетки и значения параметров."""
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
		stats = mesh_stats(mesh_data)
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
		return stats, values
	finally:
		# Сцена живёт между запросами, поэтому временные данные убираем сразу
		bpy.data.objects.remove(export_obj, do_unlink=True)
		bpy.data.meshes.remove(mesh_data)


def handle(conn):
	with conn, conn.makefile("rwb") as f:
		line = f.readline()
		if not line:
			return
		try:
			req = json.loads(line)
			out = req.pop("out")
			stats, values = generate({k: float(v) for k, v in req.items()}, out)
			time.sleep(EXTRA_DELAY)  # имитация долгого расчёта; прогрев её не затрагивает
			resp = {"ok": True, "stats": stats, "params": values}
		except Exception as e:
			traceback.print_exc()
			resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
		f.write(json.dumps(resp).encode() + b"\n")
		f.flush()


# Прогрев: первый экспорт импортирует glTF-аддон. Делаем это до публикации сокета
generate({"x": 100.0, "y": 100.0, "z": 100.0}, "/dev/shm/m_warmup.glb")
os.remove("/dev/shm/m_warmup.glb")

if os.path.exists(SOCK_PATH):
	os.remove(SOCK_PATH)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK_PATH)  # появление сокета = воркер готов
srv.listen(16)
print("worker ready", flush=True)

while True:
	conn, _ = srv.accept()
	handle(conn)
