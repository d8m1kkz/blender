<?php

return [

	// Адрес gen для запросов с сервера (MCP).
	// Не задан — берётся первый из GEN_ORIGINS (см. App\Mcp\Gen::url).
	'gen' => [
		'url' => env('GEN_URL'),
	],

];
