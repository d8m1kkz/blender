<!DOCTYPE html>
<html lang="{{ str_replace('_', '-', app()->getLocale()) }}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Laravel + Vue</title>
    @vite(['resources/css/app.css', 'resources/js/app.js'])
</head>
<body style="display: flex; justify-content: center; align-items: center; min-height: 100vh; background-color: #f8fafc; margin: 0;">
<div id="app">
    <model-viewer></model-viewer>
</div>
<div id="app">
    <counter-control></counter-control>
</div>
</body>
</html>
