<?php

use App\Mcp\Servers\BasketServer;
use Laravel\Mcp\Facades\Mcp;

// MCP-сервер корзины (Streamable HTTP): <сайт>/mcp
Mcp::web('/mcp', BasketServer::class);
