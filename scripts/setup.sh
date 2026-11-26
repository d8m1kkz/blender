#!/usr/bin/env bash
# Настройка проекта на новом компьютере (один раз после git clone).
# Использование: scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.." # корень проекта

# Включить hooks из репозитория (git сам этого не делает): scripts/hooks/pre-push
git config core.hooksPath scripts/hooks
echo "Hook включён: git push в main при изменениях gen соберёт и отправит образ."

# Эти файлы в git не хранятся, но нужны для разработки и сборки
for f in gen/blender-linux-x64.tar.xz php/.env php/.env.beget; do
	[ -f "$f" ] || echo "Не хватает файла: $f"
done
echo "Для отправки образа нужен вход в Docker Hub: docker login"
