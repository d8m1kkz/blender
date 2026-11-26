<?php

return [

	// Адрес gen для запросов с сервера (MCP). Не путать с GEN_ORIGINS — те адреса для браузера.
	// Не задан — берётся первый из GEN_ORIGINS (см. App\Mcp\Gen::url).
	'gen' => [
		'url' => env('GEN_URL'),
	],

];
