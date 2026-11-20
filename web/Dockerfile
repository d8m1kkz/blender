# ---- base: окружение PHP (используется и в dev через target: base) ----
FROM php:8.5-fpm AS base

RUN apt-get update && apt-get install -y \
	libpng-dev \
	libonig-dev \
	libxml2-dev \
	libzip-dev \
	zip \
	unzip \
	git \
	curl \
	&& docker-php-ext-install pdo_mysql mbstring exif pcntl bcmath gd zip \
	&& curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer \
	&& apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html

# ---- vendor: PHP-зависимости без dev-пакетов ----
FROM base AS vendor
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-autoloader --prefer-dist --no-interaction

# ---- assets: сборка Vite (public/build) ----
FROM node:22-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci
COPY vite.config.js ./
COPY resources ./resources
COPY public ./public
RUN npm run build

# ---- app: prod-образ php-fpm с исходниками внутри ----
FROM base AS app
COPY . .
COPY --from=vendor /var/www/html/vendor ./vendor
COPY --from=assets /app/public/build ./public/build
RUN composer dump-autoload --optimize --no-dev \
	&& mkdir -p storage/framework/cache storage/framework/sessions storage/framework/views storage/logs bootstrap/cache \
	&& chown -R www-data:www-data storage bootstrap/cache

# ---- web: prod-образ nginx со статикой из public/ ----
FROM nginx:stable AS web
COPY --from=nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=app /var/www/html/public /var/www/html/public
