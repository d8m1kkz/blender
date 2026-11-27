<?php

namespace App\Mcp\Tools;

use App\Mcp\Gen;
use App\Mcp\GenException;
use App\Mcp\Param;
use Illuminate\Contracts\JsonSchema\JsonSchema;
use Laravel\Mcp\Request;
use Laravel\Mcp\Response;
use Laravel\Mcp\ResponseFactory;
use Laravel\Mcp\Server\Tool;
use Laravel\Mcp\Server\Tools\Annotations\IsIdempotent;

/** Запись одного параметра (set_<имя>): правка общей корзины (ждёт пересчитанную модель) или общего вида (сразу). */
#[IsIdempotent]
class SetParam extends Tool
{
	/** Цвет #RRGGBB — так его проверяет gen (parse_view) */
	private const COLOR_PATTERN = '^#[0-9a-fA-F]{6}$';

	public function __construct(private Param $param)
	{
		$this->name = 'set_'.$param->name;
		$this->title = 'Задать: '.$param->title;
		$this->description = $param->view
			? "Задаёт {$param->about}. Это общий вид: меняется сразу у всех веб-клиентов, модель не пересчитывается. "
				.'Остальные параметры не меняются. Возвращает новое значение.'
			: "Задаёт {$param->about} общей корзины и ждёт, пока Blender пересчитает модель; "
				.'новую модель сразу видят все веб-клиенты. Остальные параметры не меняются. '
				.'Возвращает значение у последней рассчитанной модели.';
	}

	public function handle(Request $request): Response|ResponseFactory
	{
		$name = $this->param->name;
		try {
			$value = $request->validate([$name => $this->rules()], [
				'required' => 'укажите :attribute',
				'numeric' => ':attribute должно быть числом',
				'between' => ':attribute должно быть от :min до :max',
				'string' => ':attribute должно быть строкой',
				'regex' => ':attribute должно быть цветом вида #RRGGBB',
			])[$name];
			$value = $this->param->color ? strtolower($value) : (float) $value;
			$result = Gen::update([$this->param->key => $value], $this->param->view);
		} catch (GenException $e) {
			return Response::error($e->getMessage());
		}
		return Response::structured([
			$name => $result[$this->param->key],
			'version' => $result['version'],
		]);
	}

	/** Правила проверки значения: цвет — по образцу, число — в пределах, которые даёт gen. */
	private function rules(): array
	{
		if ($this->param->color) {
			return ['bail', 'required', 'string', 'regex:/'.self::COLOR_PATTERN.'/'];
		}
		$limit = Gen::limits($this->param->view)[$this->param->key];
		return ['required', 'numeric', "between:{$limit['min']},{$limit['max']}"];
	}

	/** Пределы берём у gen; если он недоступен — схема без них. */
	public function schema(JsonSchema $schema): array
	{
		if ($this->param->color) {
			$value = $schema->string()->pattern(self::COLOR_PATTERN)->description($this->param->about)->required();
			return [$this->param->name => $value];
		}
		$value = $schema->number()->description($this->param->about)->required();
		try {
			$limit = Gen::limits($this->param->view)[$this->param->key];
			$value->min($limit['min'])->max($limit['max']);
		} catch (GenException) {
		}
		return [$this->param->name => $value];
	}
}
