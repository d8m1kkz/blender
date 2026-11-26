<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;

$app = Application::configure(basePath: dirname(__DIR__))
	->withRouting(
		web: __DIR__.'/../routes/web.php',
	)
	->withMiddleware(function (Middleware $middleware): void {
		//
	})
	->withExceptions(function (Exceptions $exceptions): void {
		//
	})->create();

// Публичная папка — public_html (а не public)
$app->usePublicPath(dirname(__DIR__).'/public_html');

return $app;
