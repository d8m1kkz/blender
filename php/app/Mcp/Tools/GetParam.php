<?php

namespace App\Mcp\Tools;

use App\Mcp\Gen;
use App\Mcp\GenException;
use App\Mcp\Param;
use Illuminate\Contracts\JsonSchema\JsonSchema;
use Laravel\Mcp\Response;
use Laravel\Mcp\ResponseFactory;
use Laravel\Mcp\Server\Tool;
use Laravel\Mcp\Server\Tools\Annotations\IsReadOnly;

/** Чтение одного параметра (get_<имя>): значение у последней готовой модели или у общего вида, без пересчёта в Blender. */
#[IsReadOnly]
class GetParam extends Tool
{
	public function __construct(private Param $param)
	{
		$this->name = 'get_'.$param->name;
		$this->title = 'Узнать: '.$param->title;
		$this->description = "Текущее значение: {$param->about}. "
			.($param->view ? 'Это общий вид, одинаковый у всех клиентов.' : 'Это значение у модели, которую сейчас видят все клиенты.');
	}

	public function handle(): Response|ResponseFactory
	{
		try {
			$current = Gen::params($this->param->view);
		} catch (GenException $e) {
			return Response::error($e->getMessage());
		}
		return Response::structured([
			$this->param->name => $current[$this->param->key],
			'version' => $current['version'],
		]);
	}

	public function schema(JsonSchema $schema): array
	{
		return [];
	}
}
