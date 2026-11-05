import bpy, sys, json, os

args = sys.argv[sys.argv.index("--") + 1:]

obj = bpy.data.objects['Vert']
gn = obj.modifiers['GeometryNodes']
params = bpy.data.node_groups["_params"].nodes["Group Output"]

params.inputs[0].default_value = float(args[0])
params.inputs[1].default_value = float(args[1])

depsgraph = bpy.context.evaluated_depsgraph_get() # Получаем актуальный граф зависимостей
eval_obj = obj.evaluated_get(depsgraph) # Достаем объект с уже вычисленными Geometry Nodes
# Превращаем вычисленные данные в полноценный отдельный меш
mesh_data = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph)
# Создаем временный объект только ради гарантированного экспорта
export_obj = bpy.data.objects.new("ExportTemp", mesh_data)
bpy.context.scene.collection.objects.link(export_obj)
# Делаем активным ТОЛЬКО его
bpy.ops.object.select_all(action='DESELECT')
export_obj.select_set(True)
bpy.context.view_layer.objects.active = export_obj

filetemp = bpy.path.abspath("/dev/shm/m_temp.glb")
filepath = bpy.path.abspath("/dev/shm/m.glb")

""" bpy.ops.wm.stl_export(
	filepath=filetemp,
	export_selected_objects=True,
	apply_modifiers=True
)"""

bpy.ops.export_scene.gltf(
    filepath=filetemp,
    export_format='GLB',
    use_selection=True
)

with open(filetemp, 'rb') as f_in:
	with open(filepath, 'wb') as f_out:
		f_out.write(f_in.read())

if os.path.exists(filetemp):
	os.remove(filetemp)