# CLAUDE.md

Руководство для Claude Code по работе с репозиторием.

## Суть проекта

Учебный проект (без production-требований): корзина для 3D-печати, которую можно менять из разных клиентов.

Сервер `gen` хранит состояние корзины (около десяти параметров) и по нему строит 3D-модель в Blender (Geometry Nodes, формат GLB). Клиенты подключаются к `gen`, показывают текущее состояние и модель и отправляют запросы на изменение. После каждого изменения `gen` рассылает новое состояние и модель всем своим клиентам. Поэтому все клиенты одного `gen` видят одно и то же.

Клиенты бывают разные, например веб-страница (Vue + Three.js) и MCP-сервер для LLM-агентов. Каждый клиент может показывать и менять свой набор параметров.

Параметры бывают двух видов. Параметры модели пересчитываются в Blender за несколько секунд. Параметры вида (цвет, камера, вращение) применяются сразу, Blender в них не участвует.

```
Браузер (Vue + Three.js) ────WebSocket────┐
                                          ├──► gen (FastAPI) ──► Blender-воркер
LLM-агент ──► MCP-сервер (Laravel) ──HTTP─┘
```

- `nginx/` — `Dockerfile` и `nginx.conf` сервиса `nginx`: раздаёт статику (копирует её из образа `php`) и передаёт PHP-запросы в `php`.
- `php/` — Laravel + Vite + Vue, сервис `php` (php-fpm с кодом). Laravel почти ничего не делает: отдаёт страницу с компонентом `ModelViewer.vue` и список адресов `gen` (`/gens`). Ещё здесь MCP-сервер `<сайт>/mcp` (пакет `laravel/mcp`: `routes/ai.php`, `app/Mcp/`): по инструменту `get_*`/`set_*` на каждый параметр, ходит в `gen` по HTTP: параметры модели — `GET /limits`, `GET /params`, `POST /params`; параметры общего вида (вращение, камера, цвет) — `GET /view/limits`, `GET /view`, `POST /view`.
- `gen/` — сервис генерации: `web.py` (FastAPI), `worker.py` (долгоживущий Blender), `basket.blend` (сцена).
- `caddy/` — `Caddyfile` только для запасного варианта с HTTPS перед сайтом (`compose.caddy.yml`).
- `scripts/` — все скрипты `.sh`: деплой сайта (`deploy.beget.sh`) и `gen` (`deploy.vps.beget.sh`, `deploy.vps.immers.sh`), проверки (`check.begetvps.sh` — что установлено и запущено на VPS Beget, `check.poll_mem.sh` — опрос `GET /memory` развёрнутого `gen`, наименьший запас памяти).
- `.github/workflows/` — GitHub Actions: `check-gen.yml` (проверка образа `gen` в pull request в `main`), `deploy-gen.yml` (сборка образа `gen` и деплой `gen` и caddy на VPS Beget по push в `main`), `deploy-php.yml` (деплой сайта на хостинг Beget по push в `main`).
- Браузер подключается к выбранному `gen` напрямую (WebSocket); сайт лишь даёт список адресов `GEN_ORIGINS`.

## Принципы

- **Production соответствует `main`:** если следовать схеме (pull request → проверка → Merge), на сервер попадает только то, что есть в `main`.
- **`compose.yml` — production, `compose.override.yml` — только отклонения для разработки** (тома с кодом, `ports`, `--reload`, сброс `env_file`).
- **Файлы `compose_<имя>.yml` — части, а не самостоятельные системы:** подключаются только через `include` из конечных compose-файлов. Сервис `gen` описан в `compose_gen.yml` (его подключают `compose.yml` и `compose.vps.yml`); на сервер часть копируется вместе с конечным файлом.
- **Один образ везде** (`d8m1kkz/blender-nginx`, `d8m1kkz/blender-php`, `d8m1kkz/blender-gen`, тег `latest`): dev и prod различаются только compose-файлом и переменными окружения. Не заводи отдельные теги и `target` для dev. Тег `blender-gen:buildcache` — не образ, а кэш сборки для GitHub Actions.
- **С рабочего компьютера образы на Docker Hub не отправляются.** `gen` собирает и отправляет только GitHub Actions. `nginx` и `php` не отправляются вовсе (сайт ставится на хостинг исходным кодом): для `compose.yml` они собираются на месте.
- **Адреса `gen` — одна переменная `GEN_ORIGINS`** (через запятую, первый по умолчанию), задаётся только для `php`: в dev — `php/.env`, в prod (Beget и VPS) — `php/.env.beget`. Браузер ходит к ним напрямую, поэтому адрес должен быть доступен из браузера. Если страница открывается по https (Beget, вариант с caddy), `gen` тоже нужен под HTTPS.
- **Два варианта деплоя `gen` на VPS:** Beget (`scripts/deploy.vps.beget.sh`) и immers.cloud (`scripts/deploy.vps.immers.sh`). Оба разворачивают `compose.vps.yml` (`gen` и caddy). Сайт (`php`) ставится отдельно на виртуальный хостинг Beget (`scripts/deploy.beget.sh`).
- **Работа идёт в ветке `dev`, в `main` напрямую не отправляют.** `git push` в `dev` ничего не запускает. Pull request из `dev` в `main` запускает `.github/workflows/check-gen.yml`: сборка образа `gen`, запуск и ожидание готовой модели (`GET /params`); на Docker Hub и VPS ничего не отправляется. Merge (вариант «Create a merge commit») запускает `.github/workflows/deploy-gen.yml`: сборка образа `gen`, отправка на Docker Hub, `scripts/deploy.vps.beget.sh` (только VPS Beget; immers — вручную). Ошибка на любом шаге отменяет следующие. Тот же Merge отдельно запускает `.github/workflows/deploy-php.yml`: `scripts/deploy.beget.sh` (сайт); он пишет на GitHub настройки ssh-алиаса `beget` (там адрес и пользователь хостинга), берёт секреты `BEGET_SSH_KEY` (тот же ключ, что для VPS) и `APP_KEY`. Все шаги деплоя — в `deploy.vps.beget.sh` (он же запускается вручную). Версия образа не проверяется: после деплоя скрипт только ждёт ответа `GET /limits`. Секреты GitHub: `DOCKERHUB_TOKEN`, `BEGET_SSH_KEY`. Вход на VPS: ключ `~/.ssh/beget_root`; на GitHub сценарий записывает в этот файл секрет `BEGET_SSH_KEY`; проверка ключа самого VPS — см. следующий пункт; адрес VPS записан только в скрипте.
- **Ключ сервера при деплое по SSH заранее не проверяется** (учебный проект, осознанное решение ради простоты): ни файла `known_hosts` в git, ни секрета с ключом сервера на GitHub. По умолчанию, в том числе в новых скриптах деплоя, — `StrictHostKeyChecking=accept-new`: незнакомый сервер принимается без вопроса и запоминается, при смене ключа ssh отказывается подключаться (после переустановки сервера: `ssh-keygen -R <IP>`). На GitHub машина каждый раз новая, поэтому там проверки фактически нет. Исключение — immers: сервер часто новый на том же адресе, поэтому `StrictHostKeyChecking=no` и `UserKnownHostsFile=/dev/null`. Риск: тот, кто перехватит связь и притворится сервером, получит то, что отправляет скрипт (из секретов там только `APP_KEY` при деплое сайта), но не закрытый ключ. Не усложняй проверку без запроса.
- **Адрес `gen` для сервера (MCP) — `GEN_URL`** (в Docker `http://gen:8080`, задан в `compose.yml`); не задан — берётся первый из `GEN_ORIGINS` (так на Beget).
- **Общая корзина:** состояние хранится в памяти одного процесса `gen`; клиенты одного `gen` видят одну корзину, клиенты разных `gen` независимы. Работает только с одним процессом uvicorn.
- **Blender держится запущенным**, с ним говорит единственный цикл генерации (`bpy` не потокобезопасен). Устаревшие результаты отбрасываются, клиентам приходит только актуальная модель.
- **Параметры модели и их пределы заданы в одном месте** — реестр `PARAMS` в `worker.py`. `web.py` берёт список имён и пределы у воркера; веб-клиент получает пределы в WebSocket-сообщении `model` (поле `limits`), MCP — запросом `GET /limits`. Для нового параметра нужны строка в `PARAMS`, поле в `ModelViewer.vue` (`params`, `NUMBER_CONTROLS`) и строка в `App\Mcp\Param::all()` (имя для MCP и описание; x, y, z там — length, width, height).
- **Параметры общего вида** (цвет, камера, ориентация, свет, вращение) Blender не касаются: дефолты и пределы — `VIEW_DEFAULTS`/`VIEW_RANGES` в `web.py`, правка сразу рассылается клиентам сообщением `view`. В MCP из них есть только `spin`, `distance` и `color` (`rotation_speed`, `camera_distance`, `color`; флаг `view: true` в `Param::all()`, у цвета ещё `color: true` — строка `#RRGGBB` вместо числа).
- Имена объектов и нод в `basket.blend` связаны с `worker.py`: переименование сломает воркер.

## Запуск

```bash
docker compose up --build            # dev: nginx (:80), php, gen (:8080), vite (:5173)
(cd php && npm run dev)              # Vite на хосте (нужен Node), из папки php/
docker compose -f compose.yml up -d --build  # prod (http)
docker compose -f compose.yml -f compose.caddy.yml up -d --build  # prod с HTTPS через caddy (запасной вариант)
SITE_HOST=<домен> docker compose -f compose.vps.yml up -d  # только gen под HTTPS (на самом VPS, <IP>.sslip.io)
git push origin dev                           # затем на GitHub: pull request в main (проверка gen) и Merge (деплой gen и сайта)
scripts/deploy.beget.sh                       # деплой сайта (без gen) на хостинг Beget (ssh-алиас beget)
scripts/deploy.vps.beget.sh [домен]           # деплой gen и caddy на VPS Beget (ключ ~/.ssh/beget_root, root, Docker уже стоит)
scripts/deploy.vps.immers.sh [IP] [домен]     # то же на VPS immers: ставит Docker; без IP — 81.94.159.64; не скачался образ — передаёт локальный
scripts/check.begetvps.sh                     # отчёт о VPS Beget (только чтение) — на экран и в scripts/vps-report.txt
scripts/check.poll_mem.sh [адрес gen] [сек]   # следить за памятью развёрнутого gen
```

- Архив Blender (`blender-linux-x64.tar.xz`, в git его нет) при сборке `gen` берётся из `gen/`, а если его там нет — скачивается из GitHub Releases (тег `blender-5.2.3`). При смене версии Blender поменять в `gen/Dockerfile` ссылку и папку `/opt/blender/5.2`, а старый архив в `gen/` заменить или удалить: он берётся раньше ссылки.
- Настройки Laravel в prod — два файла: `php/.env.beget` (без секретов, в git) и `php/.env.beget.key` (одна строка `APP_KEY=…`, в git нет). Их берут `compose.yml` и `scripts/deploy.beget.sh` (склеивает в `.env` на сервере); `.dockerignore` их не исключает, поэтому они попадают в образ. В dev настройки из `php/.env`. `php/.env` и `php/.env.beget.key` после `git clone` нужно положить вручную.
- После правки `nginx.conf` надёжно: `docker compose up -d --force-recreate --no-deps nginx`; после правки `.env` или переменных `php` — то же для `php`.

## Соглашения

- Язык комментариев и интерфейса — русский.
- Отступы задаёт `.editorconfig` в корне. Сохраняй стиль правимого файла.
- Скрипты `.sh` клади в `scripts/`. Пути в них не должны зависеть от папки запуска: в начале скрипт сам переходит в нужную папку (для корня проекта — `cd "$(dirname "$0")/.."`).
- Новые Vue-компоненты регистрируй в `php/resources/js/app.js` и подключай в Blade тегом.
- БД приложению не нужна (сессии — cookie, кэш — file, очередь — sync).

## Известные шероховатости (не «чини» без запроса)

- Нет аутентификации: любой открывший страницу меняет корзину для всех.
- Скрипты деплоя не проверяют заранее ключ сервера по SSH (см. «Принципы») — допустимо только для учебного стенда.
- `depends_on` на `gen` не гарантирует готовность Blender (сокет появляется после прогрева).
- В `learn/` лежат учебные заметки (`dev*.md`, `new*.md`, `review*.md`, `deepen*.md`) — не часть приложения, в git их нет.
