# ---- blender-local: архив Blender из папки gen, если он там есть (в git его нет) ----
# [z] делает имя шаблоном: когда файла нет, COPY не завершается ошибкой, и этап остаётся пустым
FROM scratch AS blender-local
COPY blender-linux-x64.tar.x[z] /

# ---- base: Blender + Python-зависимости ----
FROM python:3.11-slim-bookworm AS base

# Настраиваем APT для реального кэширования пакетов
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
	&& echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

RUN  --mount=type=cache,target=/var/cache/apt,sharing=locked \
	 --mount=type=cache,target=/var/lib/apt,sharing=locked \
	 apt-get update && apt-get install -y --no-install-recommends \
		ca-certificates \
		curl \
		xz-utils \
	    libgl1 \
	    libsm6 \
	    libx11-6 \
	    libxext6 \
	    libxfixes3 \
	    libxi6 \
	    libxkbcommon0 \
	    libxrender1

# Архив Blender: из папки gen, а если его там нет — из GitHub Releases. Различается только адрес (file:// или
# https://), скачивание одинаковое: до 10 попыток, после обрыва — с того же места (-C -). Копия архива
# удаляется в этом же слое, поэтому образ не растёт.
# Если все попытки не удались, сборку остановит tar: неполный архив не распаковывается.
# При смене версии Blender поменять ссылку и папку /opt/blender/5.2, а старый архив в gen/ заменить или удалить
RUN --mount=type=bind,from=blender-local,target=/tmp/local \
	url=https://github.com/d8m1kkz/blender/releases/download/blender-5.2.3/blender-linux-x64.tar.xz \
	&& if [ -f /tmp/local/blender-linux-x64.tar.xz ]; then url=file:///tmp/local/blender-linux-x64.tar.xz; fi \
	&& echo "Blender: скачиваю $url" \
	&& for attempt in 1 2 3 4 5 6 7 8 9 10; do \
		curl -fsSL -C - -o /tmp/blender.tar.xz "$url" && break; \
		echo "Blender: попытка $attempt не удалась" && sleep 5; \
	done \
	&& mkdir -p /opt/blender \
	&& tar -xf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
	&& rm -f /tmp/blender.tar.xz \
	&& ln -s /opt/blender/blender /usr/local/bin/blender \
	&& cd /opt/blender/5.2 \
	&& rm -rf datafiles/locale datafiles/studiolights \
	          python/lib/python3.*/site-packages/pip \
	          python/lib/python3.*/site-packages/setuptools \
	          python/lib/python3.*/site-packages/Cython \
	          python/lib/python3.*/site-packages/pxr \
	&& find /opt/blender -type d -name __pycache__ -prune -exec rm -rf {} +

# uvicorn[standard]: без WebSocket-библиотеки (websockets) uvicorn не примет рукопожатие /ws;
# заодно watchfiles для --reload в dev
RUN --mount=type=cache,target=/root/.cache/pip pip install fastapi "uvicorn[standard]"

WORKDIR /app

COPY basket.blend web.py worker.py ./

# Порт берём из $PORT (его задаёт Render, по умолчанию 10000); локально и в compose — 8080.
# Shell-форма + exec: нужна подстановка переменной, а uvicorn должен остаться PID 1 и получать SIGTERM
CMD exec uvicorn web:app --host 0.0.0.0 --port ${PORT:-8080}
