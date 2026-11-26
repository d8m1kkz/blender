<?php

namespace App\Mcp;

use RuntimeException;

/** Ошибка связи с gen или ответ gen с ошибкой; текст понятен человеку и показывается клиенту MCP. */
class GenException extends RuntimeException {}
