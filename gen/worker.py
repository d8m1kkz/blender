"""Постоянно запущенный Blender-воркер.

Запуск: blender -b cube.blend --factory-startup --python worker.py
Слушает unix-сокет, принимает по одной JSON-строке {"x":..,"y":..,"out":..}
и отвечает JSON-строкой {"ok":true} или {"ok":false,"error":".."}.
Всё работает в главном потоке: bpy не потокобезопасен.
"""
import bpy, os, json, socket, traceback

SOCK_PATH = os.environ.get("BLENDER_SOCK", "/dev/shm/blender.sock")

obj = bpy.data.objects['Vert']
params = bpy.data.node_groups["_params"].nodes["Group Output"]


def generate(x, y, out):
	params.inputs[0].default_value = x
	params.inputs[1].default_value = y
	# Значения поменялись в уже загруженной сцене: помечаем объект и пересчитываем граф
	obj.update_tag()
	bpy.context.view_layer.update()

	depsgraph = bpy.context.evaluated_depsgraph_get()
	eval_obj = obj.evaluated_get(depsgraph)
	mesh_data = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph)
	export_obj = bpy.data.objects.new("ExportTemp", mesh_data)
	try:
		bpy.context.scene.collection.objects.link(export_obj)
		bpy.ops.object.select_all(action='DESELECT')
		export_obj.select_set(True)
		bpy.context.view_layer.objects.active = export_obj

		bpy.ops.export_scene.gltf(
			filepath=out,
			export_format='GLB',
			use_selection=True
		)
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
			generate(float(req["x"]), float(req["y"]), req["out"])
			resp = {"ok": True}
		except Exception as e:
			traceback.print_exc()
			resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
		f.write(json.dumps(resp).encode() + b"\n")
		f.flush()


# Прогрев: первый экспорт импортирует glTF-аддон. Делаем это до публикации сокета
generate(100.0, 100.0, "/dev/shm/m_warmup.glb")
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
