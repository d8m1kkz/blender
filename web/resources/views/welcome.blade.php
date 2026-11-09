<!DOCTYPE html>
<html lang="{{ str_replace('_', '-', app()->getLocale()) }}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Laravel + Vue</title>
    @vite(['resources/css/app.css', 'resources/js/app.js'])
</head>
<body style="display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; font-family: 'Instrument Sans', system-ui, sans-serif; background: radial-gradient(ellipse at 20% 0%, #26406b 0%, transparent 55%), radial-gradient(ellipse at 90% 100%, #3b2a6b 0%, transparent 50%), #0b1220;">
<div id="app">
    <model-viewer></model-viewer>
</div>
</body>
</html>
