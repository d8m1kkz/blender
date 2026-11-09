FROM php:8.5-fpm

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