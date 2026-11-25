<?php

use Illuminate\Support\Facades\Route;

// Список адресов gen (из GEN_ORIGINS, через запятую) — и в Docker, и на хостинге (Beget).
// Не используйте config:cache: env() вне config не увидит значение из файла .env.
Route::get('/gens', function () {
	$origins = array_values(array_filter(array_map('trim', explode(',', (string) env('GEN_ORIGINS')))));
	return response()->json($origins)->header('Cache-Control', 'no-store');
});

Route::get('/', function () {
	return view('welcome');
});
