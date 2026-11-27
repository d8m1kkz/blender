#!/usr/bin/env bash
# Деплой сайта (php/) на Beget в папку ~/blender: первый и повторный (обновление).
# Использование: scripts/deploy.beget.sh
set -euo pipefail
cd "$(dirname "$0")/../php"

# Собрать JS и CSS локально: на Beget нет Node
npm run build

# Скопировать нужное (hot и fonts-manifest.dev.json нужны только в разработке).
# --delete удаляет на сервере файлы, которых больше нет локально (только внутри этих папок).
rsync -rlptz --delete --exclude=hot --exclude=fonts-manifest.dev.json public_html/ beget:blender/public_html/
rsync -rlptz --delete app bootstrap config routes beget:blender/
rsync -rlptzR --delete resources/views beget:blender/
rsync -rlptz artisan composer.json composer.lock beget:blender/
# .env на сервере: настройки из php/.env.beget (APP_URL, GEN_ORIGINS…) и APP_KEY из php/.env.beget.key
cat .env.beget .env.beget.key | ssh beget 'cat > blender/.env'

# На сервере: поставить зависимости, создать папки и выдать права на запись.
# Команды composer-phpX.Y на Beget — это Composer 1, а Laravel 13 нужен Composer 2: скачиваем composer.phar
ssh beget '[ -f composer.phar ] || curl -fsSL https://getcomposer.org/download/latest-2.x/composer.phar -o composer.phar
	cd blender &&
	php8.5 ~/composer.phar install --no-dev --optimize-autoloader &&
	mkdir -p storage/framework/{cache,sessions,views} storage/logs &&
	chmod -R 775 storage bootstrap/cache &&
	php8.5 artisan view:clear'   # сбросить скомпилированные шаблоны Blade

echo "Готово: https://d8m1k.bget.ru"
