"""MCP-сервер корзины: ещё один клиент gen по WebSocket /ws.

Правки отсюда попадают в общую корзину, и все веб-клиенты видят новую модель.
Каждый вызов инструмента — своё короткое соединение: сразу после подключения gen
присылает текущую корзину, так что ни переподключений, ни своего состояния здесь не нужно.
"""
import os, json, math, asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import websockets
from pydantic import BaseModel, Field
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

# glb=0 — gen шлёт только заголовки моделей, без бинарных GLB (MCP они не нужны)
GEN_WS = os.environ.get("GEN_WS", "ws://gen:8080/ws") + "?glb=0"
FIRST_MODEL_TIMEOUT = 30  # с, ждём текущую корзину (gen мог только что запуститься)
RESULT_TIMEOUT = 60  # с, ждём актуальную модель после правки
VIEW_TIMEOUT = 10  # с, ждём подтверждения правки вида (Blender не нужен, gen отвечает сразу)

# Печать: PETG на Bambu Lab A1
PETG_DENSITY = 1.27  # г/см³
FILAMENT_DIAMETER = 1.75  # мм
BUILD_VOLUME = 256  # мм, рабочая область A1 — куб 256×256×256

mcp = MCPServer(
	"basket",
	instructions=(
		"Управление общей 3D-корзиной, которую видят все открытые веб-интерфейсы. "
		"Размеры в миллиметрах: width и length — ширина и длина (20–256, стол принтера Bambu Lab A1), "
		"height — высота (20–150). Корзина решётчатая; её форма: толщина прутков (thickness), высота арки "
		"в стенках (excavation_height) и шаг перфорации-решётки стенок и дна (perforation). Модель пересчитывается в Blender; "
		"новые значения и модель появляются у всех клиентов одновременно, когда расчёт закончен. "
		"Кроме размеров есть общий «вид»: цвет модели, расстояние от камеры, ориентация модели "
		"(повороты в градусах: rot_x — вокруг оси ширины, rot_y — вокруг вертикали, rot_z — вокруг оси длины; "
		"модель всегда стоит на столе нижней точкой, так что rot_x=90 кладёт её на бок, rot_x=180 переворачивает) "
		"и освещение (цвет и яркость), а также скорость вращения стола с моделью. "
		"get_state — всё сразу, reset_view — вид по умолчанию, get_print_estimate — расход пластика, "
		"get_model_glb — ссылка на скачивание текущей модели в GLB. Вид применяется мгновенно, без пересчёта в Blender."
	),
)


# Описания и диапазоны параметров формы: общие для схем входа (set_*) и выхода (get_*, Basket)
# Корзина решётчатая: стенки и дно — из прутков с просветами между ними (в сцене — «толщина прутков»,
# «высота арки», stepWall, stepBottom)
THICKNESS = "толщина прутков решётки (стенок и дна), мм"
EXCAVATION_HEIGHT = "высота арки — арочного выреза в стенках корзины, мм"
PERFORATION_WALL = "шаг перфорации стенок — расстояние между прутками решётки, мм; больше — просветы крупнее и прутков меньше"
PERFORATION_FLOOR = "шаг перфорации дна — расстояние между прутками решётки, мм; больше — просветы крупнее и прутков меньше"


class Basket(BaseModel):
	version: int = Field(description="номер версии общей корзины")
	width: float
	length: float
	height: float
	# Значения — из сцены Blender; None, если gen ещё старый и их не присылает
	thickness: float | None = Field(None, description=THICKNESS)
	excavation_height: float | None = Field(None, description=EXCAVATION_HEIGHT)
	perforation_wall: float | None = Field(None, description=PERFORATION_WALL)
	perforation_floor: float | None = Field(None, description=PERFORATION_FLOOR)


class SetResult(BaseModel):
	status: Literal["applied", "superseded"] = Field(
		description="applied — показана наша правка; superseded — пока считали, корзину изменил другой клиент",
	)
	requested_version: int = Field(description="версия, которую gen присвоил нашей правке")
	basket: Basket = Field(description="корзина, которую сейчас видят все клиенты")


SHAPE_KEYS = ("thickness", "excavation_height", "perforation_wall", "perforation_floor")


def to_basket(msg: dict) -> Basket:
	# Протокол gen называет размеры x/y/z: x — width, y — length, z — height; остальные имена совпадают
	return Basket(
		version=msg["version"], width=msg["x"], length=msg["y"], height=msg["z"],
		**{k: msg[k] for k in SHAPE_KEYS if k in msg},
	)


async def recv_until(ws, predicate) -> dict:
	"""Читает сообщения gen, пока не встретится подходящее."""
	while True:
		msg = json.loads(await ws.recv())
		if predicate(msg):
			return msg


@asynccontextmanager
async def connect():
	"""Короткое соединение с gen; понятная ошибка, если gen не отвечает или оборвал связь.

	Текст ошибки модель увидит только у ToolError: остальные исключения SDK считает
	падением и скрывает их текст от клиента.
	"""
	try:
		ws = await websockets.connect(GEN_WS, open_timeout=10)
	except (OSError, TimeoutError, websockets.InvalidHandshake) as e:
		raise ToolError(f"gen недоступен ({GEN_WS}): {e}") from e
	try:
		yield ws
	except websockets.ConnectionClosed as e:
		raise ToolError(f"gen оборвал соединение (перезапуск?): {e}") from e
	finally:
		await ws.close()


async def current(ws) -> dict:
	try:
		async with asyncio.timeout(FIRST_MODEL_TIMEOUT):
			return await recv_until(ws, lambda m: m["type"] == "model")
	except TimeoutError:
		raise ToolError("gen не прислал текущую корзину — Blender ещё прогревается?") from None


@mcp.tool()
async def get_basket() -> Basket:
	"""Текущие размеры (мм) и форма общей корзины, которую сейчас видят все веб-клиенты."""
	async with connect() as ws:
		return to_basket(await current(ws))


@mcp.tool()
async def set_basket(
	width: Annotated[float | None, Field(ge=20, le=256, description="ширина, мм; не указано — оставить как есть")] = None,
	length: Annotated[float | None, Field(ge=20, le=256, description="длина, мм; не указано — оставить как есть")] = None,
	height: Annotated[float | None, Field(ge=20, le=150, description="высота, мм; не указано — оставить как есть")] = None,
) -> SetResult:
	"""Меняет размеры общей корзины и ждёт, пока все веб-клиенты получат новую модель.

	Неуказанные размеры остаются прежними. Если за время расчёта корзину изменил
	кто-то другой, вернётся status="superseded" и та корзина, которую видят все.
	"""
	patch = {k: v for k, v in (("x", width), ("y", length), ("z", height)) if v is not None}
	if not patch:
		raise ToolError("укажите хотя бы один из размеров: width, length или height")
	return await set_params(**patch)


async def set_params(**patch) -> SetResult:
	"""Шлёт частичную правку параметров модели (имена протокола gen) и ждёт пересчитанную модель.

	Неуказанные параметры gen оставляет прежними, поэтому текущую корзину заранее не читаем.
	"""
	async with connect() as ws:
		await ws.send(json.dumps({"type": "params", **patch, "seq": 1}))

		version = None
		try:
			async with asyncio.timeout(RESULT_TIMEOUT):
				while True:
					msg = json.loads(await ws.recv())
					if msg["type"] == "ack":
						version = msg["version"]
						continue
					# Всё, что пришло до ack или старше нашей версии, к нашей правке не относится
					if version is None or msg.get("version", 0) < version:
						continue
					if msg["type"] == "error":
						raise ToolError(f"генерация v{msg['version']} не удалась: {msg['error']}")
					if msg["type"] == "model":
						# gen присылает только актуальные модели: версия новее нашей — правку перекрыли
						status = "applied" if msg["version"] == version else "superseded"
						return SetResult(status=status, requested_version=version, basket=to_basket(msg))
		except TimeoutError:
			raise ToolError(
				f"правка принята как v{version}, но актуальная модель не появилась за {RESULT_TIMEOUT} с — "
				"возможно, корзину непрерывно меняют из веб-интерфейса"
			) from None


# ── Форма корзины: толщина, выемка, перфорация (пересчёт в Blender, как и размеры) ──

class Thickness(BaseModel):
	version: int = Field(description="номер версии общей корзины")
	thickness: float = Field(description=THICKNESS)


class ExcavationHeight(BaseModel):
	version: int = Field(description="номер версии общей корзины")
	excavation_height: float = Field(description=EXCAVATION_HEIGHT)


class Perforation(BaseModel):
	version: int = Field(description="номер версии общей корзины")
	wall: float = Field(description=PERFORATION_WALL)
	floor: float = Field(description=PERFORATION_FLOOR)


async def current_shape(*keys: str) -> dict:
	"""Текущая модель; ToolError, если gen не прислал нужных значений из Blender."""
	async with connect() as ws:
		msg = await current(ws)
	missing = [k for k in keys if msg.get(k) is None]
	if missing:
		raise ToolError(f"gen не прислал {', '.join(missing)} — обновлены ли gen/web.py, gen/worker.py и basket.blend?")
	return msg


@mcp.tool()
async def get_thickness() -> Thickness:
	"""Текущая толщина прутков решётки корзины (мм), как она стоит в сцене Blender."""
	msg = await current_shape("thickness")
	return Thickness(version=msg["version"], thickness=msg["thickness"])


@mcp.tool()
async def set_thickness(
	thickness: Annotated[float, Field(ge=0.4, le=10, description=f"{THICKNESS}; по умолчанию 1,5; меньше 0,8 (два прохода сопла 0,4) печатается ненадёжно")],
) -> SetResult:
	"""Меняет толщину прутков решётки и ждёт, пока все веб-клиенты получат пересчитанную модель.

	Толще прутки — прочнее и тяжелее корзина (см. get_print_estimate); внешние размеры не меняются.
	"""
	return await set_params(thickness=thickness)


@mcp.tool()
async def get_excavation_height() -> ExcavationHeight:
	"""Текущая высота арки в стенках корзины (мм), как она стоит в сцене Blender."""
	msg = await current_shape("excavation_height")
	return ExcavationHeight(version=msg["version"], excavation_height=msg["excavation_height"])


@mcp.tool()
async def set_excavation_height(
	height: Annotated[float, Field(ge=0, le=150, description=f"{EXCAVATION_HEIGHT}; по умолчанию 10; не больше высоты корзины")],
) -> SetResult:
	"""Меняет высоту арки в стенках и ждёт, пока все веб-клиенты получат пересчитанную модель."""
	return await set_params(excavation_height=height)


@mcp.tool()
async def get_perforation() -> Perforation:
	"""Текущий шаг перфорации (решётки) стенок и дна корзины (мм), как он стоит в сцене Blender."""
	msg = await current_shape("perforation_wall", "perforation_floor")
	return Perforation(version=msg["version"], wall=msg["perforation_wall"], floor=msg["perforation_floor"])


@mcp.tool()
async def set_perforation(
	wall: Annotated[float | None, Field(ge=2, le=100, description=f"{PERFORATION_WALL}; по умолчанию 15; не указано — оставить")] = None,
	floor: Annotated[float | None, Field(ge=2, le=100, description=f"{PERFORATION_FLOOR}; по умолчанию 10; не указано — оставить")] = None,
) -> SetResult:
	"""Меняет шаг перфорации (решётки) стенок и/или дна; ждёт пересчитанную модель у всех клиентов.

	Неуказанное остаётся прежним. Крупный шаг облегчает корзину, но делает её менее жёсткой,
	а мелкие вещи в ней провалятся; мелкий шаг — плотнее и тяжелее.
	"""
	patch = {k: v for k, v in (("perforation_wall", wall), ("perforation_floor", floor)) if v is not None}
	if not patch:
		raise ToolError("укажите хотя бы одно: wall или floor")
	return await set_params(**patch)


# ── Общий вид: цвет, камера, ориентация, свет (мгновенно, без Blender) ──

HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class Color(BaseModel):
	color: str = Field(description="цвет пластика модели, #RRGGBB")


class Distance(BaseModel):
	distance: float = Field(description="расстояние от камеры до центра стола, мм (больше — дальше)")


class Orientation(BaseModel):
	rot_x: float = Field(description="поворот вокруг оси ширины, градусы (±90 — лечь на бок, 180 — вверх дном)")
	rot_y: float = Field(description="поворот вокруг вертикальной оси, градусы")
	rot_z: float = Field(description="поворот вокруг оси длины, градусы")


class Lighting(BaseModel):
	color: str = Field(description="цвет направленного света, #RRGGBB")
	brightness: float = Field(description="множитель яркости освещения: 1 — по умолчанию, 0 — темно")


class Spin(BaseModel):
	spin: float = Field(description="скорость вращения, градусы в секунду; 0 — не вращается, знак — направление")


async def get_view() -> dict:
	async with connect() as ws:
		return await view_of(ws)


async def view_of(ws) -> dict:
	try:
		async with asyncio.timeout(FIRST_MODEL_TIMEOUT):
			return await recv_until(ws, lambda m: m["type"] == "view")
	except TimeoutError:
		raise ToolError("gen не прислал текущий вид") from None


async def set_view(**patch) -> dict:
	"""Шлёт частичную правку вида и ждёт, пока gen её применит и разошлёт (версия вида вырастет)."""
	async with connect() as ws:
		before = await view_of(ws)
		await ws.send(json.dumps({"type": "view", **patch}))
		try:
			async with asyncio.timeout(VIEW_TIMEOUT):
				return await recv_until(ws, lambda m: m["type"] == "view" and m["version"] > before["version"])
		except TimeoutError:
			raise ToolError("gen не подтвердил изменение вида — значения вне допустимых границ?") from None


def to_orientation(v: dict) -> Orientation:
	return Orientation(rot_x=v["rot_x"], rot_y=v["rot_y"], rot_z=v["rot_z"])


def to_lighting(v: dict) -> Lighting:
	return Lighting(color=v["light_color"], brightness=v["light_brightness"])


@mcp.tool()
async def get_color() -> Color:
	"""Текущий цвет модели (#RRGGBB)."""
	return Color(color=(await get_view())["color"])


@mcp.tool()
async def set_color(color: Annotated[str, Field(pattern=HEX_COLOR, description="цвет пластика, #RRGGBB, например #ff8800")]) -> Color:
	"""Меняет цвет модели у всех веб-клиентов."""
	return Color(color=(await set_view(color=color))["color"])


@mcp.tool()
async def get_distance() -> Distance:
	"""Расстояние от камеры до центра стола, как его последний раз задали (мм).

	Если кто-то приближал колесом мыши в браузере, у него расстояние локально другое.
	"""
	return Distance(distance=(await get_view())["distance"])


@mcp.tool()
async def set_distance(distance: Annotated[float, Field(ge=50, le=5000, description="расстояние, мм; больше — модель дальше")]) -> Distance:
	"""Отодвигает или приближает камеру у всех веб-клиентов (направление взгляда сохраняется)."""
	return Distance(distance=(await set_view(distance=distance))["distance"])


@mcp.tool()
async def get_orientation() -> Orientation:
	"""Текущая ориентация модели в пространстве (повороты в градусах)."""
	return to_orientation(await get_view())


@mcp.tool()
async def set_orientation(
	rot_x: Annotated[float | None, Field(ge=-360, le=360, description="вокруг оси ширины, градусы; 90 — лечь на бок, 180 — перевернуть; не указано — оставить")] = None,
	rot_y: Annotated[float | None, Field(ge=-360, le=360, description="вокруг вертикали, градусы; не указано — оставить")] = None,
	rot_z: Annotated[float | None, Field(ge=-360, le=360, description="вокруг оси длины, градусы; не указано — оставить")] = None,
) -> Orientation:
	"""Поворачивает модель; абсолютные углы, не прибавка к текущим. Модель остаётся стоять на столе.

	Неуказанные оси остаются прежними. Все нули — исходное положение.
	"""
	patch = {k: v for k, v in (("rot_x", rot_x), ("rot_y", rot_y), ("rot_z", rot_z)) if v is not None}
	if not patch:
		raise ToolError("укажите хотя бы один из углов: rot_x, rot_y или rot_z")
	return to_orientation(await set_view(**patch))


@mcp.tool()
async def get_lighting() -> Lighting:
	"""Текущие цвет и яркость освещения."""
	return to_lighting(await get_view())


@mcp.tool()
async def set_lighting(
	color: Annotated[str | None, Field(pattern=HEX_COLOR, description="цвет направленного света, #RRGGBB; не указано — оставить")] = None,
	brightness: Annotated[float | None, Field(ge=0, le=10, description="множитель яркости: 1 — по умолчанию, 0 — темно, до 10; не указано — оставить")] = None,
) -> Lighting:
	"""Меняет цвет и/или яркость освещения у всех веб-клиентов. Неуказанное остаётся прежним.

	Цвет окрашивает направленный свет; общий фон-«комната» нейтральный, поэтому оттенок деликатный.
	"""
	patch = {}
	if color is not None:
		patch["light_color"] = color
	if brightness is not None:
		patch["light_brightness"] = brightness
	if not patch:
		raise ToolError("укажите хотя бы одно: color или brightness")
	return to_lighting(await set_view(**patch))


@mcp.tool()
async def get_spin() -> Spin:
	"""Текущая скорость автовращения корзины (градусы в секунду)."""
	return Spin(spin=(await get_view())["spin"])


@mcp.tool()
async def set_spin(spin: Annotated[float, Field(ge=-360, le=360, description="градусы в секунду; 0 — остановить; знак задаёт направление; по умолчанию −3.44")]) -> Spin:
	"""Задаёт скорость вращения корзины на столе у всех веб-клиентов (0 останавливает)."""
	return Spin(spin=(await set_view(spin=spin))["spin"])


# ── Сводка, сброс, оценка печати, снимок ──

class State(BaseModel):
	basket: Basket = Field(description="размеры (мм) и форма корзины")
	color: str = Field(description="цвет модели, #RRGGBB")
	distance: float = Field(description="расстояние камеры, мм")
	orientation: Orientation
	lighting: Lighting
	spin: float = Field(description="скорость вращения, градусы в секунду")


class PrintEstimate(BaseModel):
	version: int = Field(description="версия корзины, для которой посчитано")
	volume_cm3: float = Field(description="объём пластика, см³ (сплошная модель, как напечатана при 100 % заполнения)")
	mass_g: float = Field(description="масса PETG, г")
	filament_m: float = Field(description="длина прутка 1,75 мм, м")
	surface_cm2: float = Field(description="площадь поверхности, см²")
	size_mm: list[float] = Field(description="габариты модели [ширина, длина, высота], мм")
	fits_bed: bool = Field(description="помещается ли в рабочую область A1 (256×256×256 мм)")
	open_edges: int = Field(description="незамкнутых рёбер сетки: 0 — замкнута; единицы-десятки на тысячи рёбер на объём почти не влияют")


@mcp.tool()
async def get_state() -> State:
	"""Всё о корзине одним вызовом: размеры, форма (толщина, выемка, перфорация) и весь вид."""
	async with connect() as ws:
		got = {}
		try:
			async with asyncio.timeout(FIRST_MODEL_TIMEOUT):
				# После подключения gen шлёт и вид, и текущую модель — порядок не важен
				while len(got) < 2:
					msg = json.loads(await ws.recv())
					if msg["type"] in ("model", "view"):
						got[msg["type"]] = msg
		except TimeoutError:
			raise ToolError("gen не прислал текущее состояние — Blender ещё прогревается?") from None
	v = got["view"]
	return State(
		basket=to_basket(got["model"]), color=v["color"], distance=v["distance"],
		orientation=to_orientation(v), lighting=to_lighting(v), spin=v["spin"],
	)


@mcp.tool()
async def reset_view() -> State:
	"""Возвращает вид к умолчанию: цвет, камера, ориентация, свет и вращение. Размеры корзины не меняются."""
	await set_view(reset=True)
	return await get_state()


@mcp.tool()
async def get_print_estimate() -> PrintEstimate:
	"""Расход PETG на печать текущей корзины: объём, масса, длина прутка, влезет ли на A1.

	Считается по сетке сплошняком, без учёта заполнения, поддержек и юбки — для тонкостенной
	корзины близко к правде, для толстых стенок слайсер даст меньше.
	"""
	async with connect() as ws:
		msg = await current(ws)
	stats = msg.get("stats")
	if not stats:
		raise ToolError("gen не прислал статистику модели — обновлён ли gen/worker.py?")
	volume = stats["volume_mm3"]
	size = [round(s, 1) for s in stats["size_mm"]]
	filament_area = math.pi * (FILAMENT_DIAMETER / 2) ** 2  # мм²
	return PrintEstimate(
		version=msg["version"],
		volume_cm3=round(volume / 1000, 2),
		mass_g=round(volume / 1000 * PETG_DENSITY, 1),
		filament_m=round(volume / filament_area / 1000, 2),
		surface_cm2=round(stats["area_mm2"] / 100, 1),
		size_mm=size,
		fits_bed=all(s <= BUILD_VOLUME for s in size),
		open_edges=stats["open_edges"],
	)


class ModelFile(BaseModel):
	url: str = Field(description="ссылка на скачивание GLB")
	version: int = Field(description="версия корзины на момент запроса")
	basket: Basket
	size_kb: float = Field(description="размер файла, КБ")


def public_base(ctx: Context) -> str:
	"""Адрес сайта, как его видит клиент: Host от nginx, схема — от caddy (X-Forwarded-Proto) в prod."""
	req = ctx.request_context.request
	if req is None or "host" not in req.headers:
		raise ToolError("не удалось определить адрес сайта для ссылки")
	proto = req.headers.get("x-forwarded-proto", req.url.scheme)
	return f"{proto}://{req.headers['host']}"


@mcp.tool()
async def get_model_glb(ctx: Context) -> ModelFile:
	"""Ссылка на скачивание текущей модели корзины в формате GLB (glTF, для просмотрщиков и Blender).

	По ссылке всегда лежит актуальная модель: если корзину изменят до скачивания, придёт новая —
	её версия и размеры есть в имени файла (basket_v<версия>_<ширина>x<длина>x<высота>.glb).
	"""
	async with connect() as ws:
		msg = await current(ws)
	return ModelFile(
		url=f"{public_base(ctx)}/model.glb?v={msg['version']}",  # v — только против кэша, gen его не смотрит
		version=msg["version"],
		basket=to_basket(msg),
		size_kb=round(msg.get("glb_bytes", 0) / 1024, 1),
	)


if __name__ == "__main__":
	mcp.run(
		"streamable-http",
		host="0.0.0.0",
		port=8000,
		stateless_http=True,  # инструментам не нужна сессия: каждый вызов самостоятелен
		# Host проверяет nginx перед нами; иначе SDK пустил бы только Host: localhost
		transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
	)
