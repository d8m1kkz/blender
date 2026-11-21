# MCP-сервер корзины: клиент gen по WebSocket, наружу — MCP по Streamable HTTP (:8000/mcp)
# 3.13+: компилятор сам убирает отступы в docstring — описания инструментов приходят клиенту без табов
FROM python:3.13-slim-bookworm

RUN --mount=type=cache,target=/root/.cache/pip pip install "mcp==2.3.0" "websockets==15.0.1"

WORKDIR /app

COPY server.py ./

CMD ["python", "server.py"]
