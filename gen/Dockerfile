FROM python:3.11-slim-bookworm

# Настраиваем APT для реального кэширования пакетов
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

RUN  --mount=type=cache,target=/var/cache/apt,sharing=locked \
     --mount=type=cache,target=/var/lib/apt,sharing=locked \
     apt-get update && apt-get install -y --no-install-recommends \
		ca-certificates \
		xz-utils \
	    libgl1 \
	    libsm6 \
	    libx11-6 \
	    libxext6 \
	    libxfixes3 \
	    libxi6 \
	    libxkbcommon0 \
	    libxrender1

RUN --mount=type=bind,source=blender-linux-x64.tar.xz,target=/tmp/blender.tar.xz \
	mkdir -p /opt/blender \
	&& tar -xf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
	&& ln -s /opt/blender/blender /usr/local/bin/blender

RUN --mount=type=cache,target=/root/.cache/pip pip install fastapi uvicorn

WORKDIR /app

CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]