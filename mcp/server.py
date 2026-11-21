"""MCP-сервер корзины: ещё один клиент gen по WebSocket /ws.

Правки отсюда попадают в общую корзину, и все веб-клиенты видят новую модель.
Каждый вызов инструмента — своё короткое соединение: сразу после подключения gen
присылает текущую корзину, так что ни переподключений, ни своего состояния здесь не нужно.
"""
import os, json, asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import websockets
from pydantic import BaseModel, Field
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

# glb=0 — gen шлёт только заголовки моделей, без бинарных GLB (MCP они не нужны)
GEN_WS = os.environ.get("GEN_WS", "ws://gen:8080/ws") + "?glb=0"
FIRST_MODEL_TIMEOUT = 30  # с, ждём текущую корзину (gen мог только что запуститься)
RESULT_TIMEOUT = 60  # с, ждём актуальную модель после правки

mcp = MCPServer(
	"basket",
	instructions=(
		"Управление общей 3D-корзиной, которую видят все открытые веб-интерфейсы. "
		"Размеры в миллиметрах: x и y — длина и ширина (20–256, стол принтера Bambu Lab A1), "
		"z — высота (20–150). Модель пересчитывается в Blender; новые размеры и модель "
		"появляются у всех клиентов одновременно, когда расчёт закончен."
	),
)


class Basket(BaseModel):
	version: int = Field(description="номер версии общей корзины")
	x: float
	y: float
	z: float


class SetResult(BaseModel):
	status: Literal["applied", "superseded"] = Field(
		description="applied — показана наша правка; superseded — пока считали, корзину изменил другой клиент",
	)
	requested_version: int = Field(description="версия, которую gen присвоил нашей правке")
	basket: Basket = Field(description="корзина, которую сейчас видят все клиенты")


def to_basket(msg: dict) -> Basket:
	return Basket(version=msg["version"], x=msg["x"], y=msg["y"], z=msg["z"])


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
	"""Текущие размеры общей корзины (мм), которую сейчас видят все веб-клиенты."""
	async with connect() as ws:
		return to_basket(await current(ws))


@mcp.tool()
async def set_basket(
	x: Annotated[float | None, Field(ge=20, le=256, description="длина, мм; не указано — оставить как есть")] = None,
	y: Annotated[float | None, Field(ge=20, le=256, description="ширина, мм; не указано — оставить как есть")] = None,
	z: Annotated[float | None, Field(ge=20, le=150, description="высота, мм; не указано — оставить как есть")] = None,
) -> SetResult:
	"""Меняет размеры общей корзины и ждёт, пока все веб-клиенты получат новую модель.

	Неуказанные размеры остаются прежними. Если за время расчёта корзину изменил
	кто-то другой, вернётся status="superseded" и та корзина, которую видят все.
	"""
	if x is None and y is None and z is None:
		raise ToolError("укажите хотя бы один из размеров: x, y или z")

	async with connect() as ws:
		cur = await current(ws)
		params = {
			"x": cur["x"] if x is None else x,
			"y": cur["y"] if y is None else y,
			"z": cur["z"] if z is None else z,
		}
		await ws.send(json.dumps({"type": "params", **params, "seq": 1}))

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


if __name__ == "__main__":
	mcp.run(
		"streamable-http",
		host="0.0.0.0",
		port=8000,
		stateless_http=True,  # инструментам не нужна сессия: каждый вызов самостоятелен
		# Host проверяет nginx перед нами; иначе SDK пустил бы только Host: localhost
		transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
	)
