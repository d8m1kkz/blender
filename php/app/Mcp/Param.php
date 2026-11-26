<?php

namespace App\Mcp;

/**
 * Параметр корзины для MCP: имя в инструментах, имя в протоколе gen и описание для модели.
 * Пределы здесь не хранятся: их источник — gen (параметры модели — реестр PARAMS в gen/worker.py,
 * параметры вида — VIEW_RANGES в gen/web.py).
 */
final readonly class Param
{
	public function __construct(
		public string $name,  // имя в MCP: get_<name>, set_<name>, аргумент set_<name>
		public string $key,   // имя в gen (x, y, z, thickness…)
		public string $title, // по-русски, для заголовков
		public string $about, // что это и в чём измеряется
		public bool $view = false, // параметр общего вида (цвет, камера, вращение): Blender не пересчитывает модель
		public bool $color = false, // значение — цвет строкой #rrggbb, а не число (пределов нет)
	) {}

	/** Все параметры, в порядке инструментов. */
	public static function all(): array
	{
		return [
			new self('length', 'x', 'длина', 'длина корзины (по оси X), мм'),
			new self('width', 'y', 'ширина', 'ширина корзины (по оси Y), мм'),
			new self('height', 'z', 'высота', 'высота корзины (по оси Z), мм'),
			new self('thickness', 'thickness', 'толщина стенки', 'толщина стенки и прутков решётки, мм'),
			new self('excavation_height', 'excavation_height', 'высота выемки', 'высота выемки (арки) в стенках корзины, мм'),
			new self('perforation', 'perforation', 'перфорация', 'шаг перфорации (решётки) стенок и дна: больше — просветы крупнее'),
			new self('rotation_speed', 'spin', 'скорость вращения',
				'скорость автовращения стола с моделью, градусы в секунду: 0 — стоит, '
				.'больше 0 — против часовой стрелки, меньше 0 — по часовой (если смотреть сверху)', view: true),
			new self('camera_distance', 'distance', 'расстояние до камеры',
				'расстояние от камеры до центра стола с моделью, мм: больше — модель меньше на экране', view: true),
			new self('color', 'color', 'цвет корзины',
				'цвет пластика корзины строкой #RRGGBB (шестнадцатеричный RGB, например #29a546)', view: true, color: true),
		];
	}
}
