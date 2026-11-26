<?php

namespace App\Mcp;

use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Http\Client\Response;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Http;

/**
 * HTTP-клиент сервиса gen: пределы, текущие значения и правка параметров корзины и общего вида.
 *
 * Параметры здесь называются как в протоколе gen (x, y, z, thickness…, spin, distance).
 * У каждого метода флаг $view: false — параметры модели (/limits, /params), true — общий вид (/view/limits, /view).
 * Ошибки связи и ответы gen с ошибкой превращаются в GenException с понятным текстом.
 */
class Gen
{
	/** Пределы на время одного запроса, по группе: schema() зовут у каждого инструмента, а gen может не отвечать */
	private static array $limits = [];

	/**
	 * Адрес gen для сервера (не для браузера): GEN_URL, иначе первый из GEN_ORIGINS.
	 * В Docker GEN_ORIGINS указывает на localhost:8080 хоста — изнутри контейнера php он недоступен.
	 */
	public static function url(): string
	{
		$url = config('services.gen.url') ?: trim(explode(',', (string) env('GEN_ORIGINS'))[0]);
		if ($url === '') {
			throw new GenException('не задан адрес gen (GEN_URL или GEN_ORIGINS)');
		}
		return rtrim($url, '/');
	}

	/** Пределы {x: {min, max}, …} из gen (GET /limits или /view/limits); на минуту кэшируются в файловом кэше. */
	public static function limits(bool $view = false): array
	{
		$path = $view ? '/view/limits' : '/limits';
		if ((self::$limits[$path] ?? null) === false) {
			throw new GenException('gen недоступен: не удалось получить пределы');
		}
		try {
			return self::$limits[$path] ??= Cache::remember('gen'.$path.'.'.md5(self::url()), 60, fn () => self::send('get', $path)->json());
		} catch (GenException $e) {
			self::$limits[$path] = false; // в этом же запросе не ждём gen повторно
			throw $e;
		}
	}

	/** Текущие значения {version, x, y, …} или {version, spin, distance, …} без пересчёта модели. */
	public static function params(bool $view = false): array
	{
		return self::send('get', $view ? '/view' : '/params')->json();
	}

	/**
	 * Частичная правка параметров (POST /params или /view).
	 * Модель: gen ждёт готовую модель, ответ — параметры последней рассчитанной {version, x, y, …};
	 * ошибка расчёта — GenException. Вид: применяется сразу, ответ — общий вид после правки.
	 */
	public static function update(array $patch, bool $view = false): array
	{
		return self::send('post', $view ? '/view' : '/params', $patch, timeout: 25)->json();
	}

	private static function send(string $method, string $path, array $data = [], int $timeout = 10): Response
	{
		try {
			$response = self::http($timeout)->{$method}($path, $data);
		} catch (ConnectionException $e) {
			throw new GenException('gen недоступен ('.self::url().'): '.$e->getMessage(), previous: $e);
		}
		if ($response->failed()) {
			// FastAPI кладёт текст ошибки в detail
			$detail = $response->json('detail');
			throw new GenException('gen ответил '.$response->status().': '.(is_string($detail) ? $detail : $response->body()));
		}
		return $response;
	}

	private static function http(int $timeout): PendingRequest
	{
		return Http::baseUrl(self::url())->acceptJson()->asJson()->timeout($timeout);
	}
}
