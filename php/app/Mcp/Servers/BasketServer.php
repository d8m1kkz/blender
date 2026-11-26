<?php

namespace App\Mcp\Servers;

use App\Mcp\Param;
use App\Mcp\Tools\GetParam;
use App\Mcp\Tools\SetParam;
use Laravel\Mcp\Server;
use Laravel\Mcp\Server\Attributes\Instructions;
use Laravel\Mcp\Server\Attributes\Name;
use Laravel\Mcp\Server\Attributes\Version;

/**
 * MCP-сервер корзины (адрес <сайт>/mcp, см. routes/ai.php). Сам состояния не хранит:
 * каждый вызов — HTTP-запрос к gen, поэтому правки попадают в ту же общую корзину, что видят браузеры.
 */
#[Name('basket')]
#[Version('1.0.0')]
#[Instructions(
	'Управление общей 3D-корзиной для печати, которую видят все открытые веб-интерфейсы. '
	.'У каждого параметра инструменты get_<имя> и set_<имя>. Параметры модели: length (длина), width (ширина), '
	.'height (высота), thickness (толщина стенки), excavation_height (высота выемки в стенках), '
	.'perforation (шаг перфорации); модель пересчитывается в Blender за несколько секунд. '
	.'Параметры вида (меняются сразу, без пересчёта): rotation_speed (скорость автовращения, градусы в секунду), '
	.'camera_distance (расстояние от камеры до модели), color (цвет корзины, #RRGGBB). Размеры в миллиметрах; допустимые пределы — в схеме set_<имя>. '
	.'Новые значения появляются у всех клиентов одновременно.'
)]
class BasketServer extends Server
{
	/** Все инструменты одной страницей tools/list (по умолчанию 15, а их больше; не все клиенты запрашивают следующие страницы) */
	public int $defaultPaginationLength = 50;

	protected function boot(): void
	{
		// Инструменты одинаковы для всех параметров: два класса, по экземпляру на параметр
		foreach (Param::all() as $param) {
			$this->tools[] = new GetParam($param);
			$this->tools[] = new SetParam($param);
		}
	}
}
