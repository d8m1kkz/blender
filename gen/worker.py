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


def generate(x, y, z, out):
	"""Пересчитывает корзину, пишет GLB в out и возвращает статистику сетки."""
	dimensions.nodes["Value"].outputs[0].default_value = x
	dimensions.nodes["Value.001"].outputs[0].default_value = y
	dimensions.nodes["Group Output"].inputs[3].default_value = z
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
		return stats
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
			stats = generate(float(req["x"]), float(req["y"]), float(req["z"]), req["out"])
			time.sleep(EXTRA_DELAY)  # имитация долгого расчёта; прогрев её не затрагивает
			resp = {"ok": True, "stats": stats}
		except Exception as e:
			traceback.print_exc()
			resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
		f.write(json.dumps(resp).encode() + b"\n")
		f.flush()


# Прогрев: первый экспорт импортирует glTF-аддон. Делаем это до публикации сокета
generate(100.0, 100.0, 100.0, "/dev/shm/m_warmup.glb")
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
