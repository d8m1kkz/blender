<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

// ── Константы ──

// Журнал полученных моделей
const MAX_HISTORY = 20

// Скорость автовращения: радиан за кадр (при 60 FPS ≈ 17°/с); знак — направление
const ROTATION_SPEED = -0.001

// Пауза перед повторным подключением к генератору, мс (пока Blender прогревается, gen не отвечает)
const RECONNECT_DELAY = 1000

// Слои печати
const LAYER_HEIGHT = 0.2 // толщина слоя, мм (единицы сцены)
const LAYER_DEPTH = 0.01 // глубина бороздки между слоями, мм
// Затухание против ряби: полоски видны, только когда слой занимает достаточно пикселей.
const LAYER_FADE_FROM_PX = 3 // меньше стольких пикселей на слой — полосок нет совсем
const LAYER_FADE_TO_PX = 20 // от стольких пикселей на слой — полоски видны полностью

// Рендер и освещение
const MAX_PIXEL_RATIO = 2 // предел плотности пикселей (экономит GPU на retina)
const TONE_MAPPING_EXPOSURE = 1 // экспозиция сцены: общая яркость
const ENVIRONMENT_INTENSITY = 5 // сила света от «комнаты» (IBL)
const LIGHT_COLOR = 0xfff0dd // цвет направленного света (тёплый)
const LIGHT_INTENSITY = 1 // интенсивность направленного света
const LIGHT_ANGLE = THREE.MathUtils.degToRad(20) // отклонение света от взгляда камеры

// Стол принтера
const BED_SIZE = 256 // стол Bambu Lab A1: 256×256 (единицы сцены = мм)
const BED_CELL = 25 // шаг сетки


// Материал PETG: зелёный глянцевый пластик с лёгким лаковым слоем (блики от печати/самого пластика).
// Один общий материал на все меши модели; цвет и шероховатость подбираются здесь.
const petgMaterial = new THREE.MeshPhysicalMaterial({
	color: 0x29A546,
	metalness: 0,
	roughness: 0.5,
	clearcoat: 0.3,
	clearcoatRoughness: 0.8,
})

// Стены «комнаты»: градиент сверху вниз, [позиция 0..1, цвет]
// Нейтральные — для освещения (IBL)
const ROOM_WALLS_LIGHTING = [
	[0, '#5c616b'], // потолок
	[0.35, '#474c55'],
	[0.5, '#3a3e46'], // горизонт
	[0.7, '#2a2c32'],
	[1, '#1d1e22'], // пол
]
// В цвет карточек интерфейса (#1e2d44 → #16212f) — для видимого фона
const ROOM_WALLS_BACKGROUND = [
	[0, '#34496a'], // потолок
	[0.35, '#263753'],
	[0.5, '#1e2d44'], // горизонт
	[0.7, '#16212f'],
	[1, '#0e1520'], // пол
]

const canvasContainer = ref(null)
// Параметры модели (имена — как в PARAMS у gen/worker.py), пределы берутся у gen (limits).
// null — значение неизвестно, пока gen не пришлёт первую модель (оно берётся из сцены Blender): поле не показываем.
const params = reactive({ x: 100, y: 100, z: 50, thickness: null, excavation_height: null, perforation: null })
// Последнее допустимое значение каждого поля: к нему возвращаем, если ввод стёрли или он не число
const lastValid = {}
// Горизонтальные слайдеры; слайдер Z — отдельный, вертикальный
const TOP_SLIDERS = ['x', 'y']
// Числовые поля. Подписи — только во всплывающей подсказке; step — шаг кнопок (у толщины дробные значения)
const NUMBER_CONTROLS = [
	{ key: 'thickness', title: 'Толщина стенки, мм', step: 0.1 },
	{ key: 'excavation_height', title: 'Высота выемки, мм', step: 1 },
	{ key: 'perforation', title: 'Перфорация', step: 1 },
]

// turntable — «поворотный столик»: в нём стол принтера и модель, автовращение крутит его целиком
let scene, camera, renderer, controls, turntable, bed, currentModel, light, resizeObserver
const loader = new GLTFLoader()

// Общий вид (цвет, камера, ориентация, свет): приходит от gen сообщением «view».
// Здесь последнее полученное — новой модели сразу выставляется текущая ориентация.
let viewState = null
let rotationSpeed = ROTATION_SPEED // автовращение, радиан за кадр; меняется сообщением «view»

// Применяет общий вид к сцене. Модель при повороте остаётся «на столе»: стол уходит под её нижнюю точку.
const applyView = (v) => {
	viewState = v
	rotationSpeed = THREE.MathUtils.degToRad(v.spin) / 60 // из градусов в секунду (кадр ≈ 1/60 с)
	petgMaterial.color.set(v.color)
	light.color.set(v.light_color)
	light.intensity = LIGHT_INTENSITY * v.light_brightness
	scene.environmentIntensity = ENVIRONMENT_INTENSITY * v.light_brightness
	if (currentModel) {
		orientModel(currentModel)
		layerUniforms.uLayerBase.value = placeBed(bed, currentModel)
	}
	// Камера: то же направление на цель, другая дистанция
	const offset = camera.position.clone().sub(controls.target)
	camera.position.copy(controls.target).addScaledVector(offset.normalize(), v.distance)
	controls.update()
}

// Ориентация модели: повороты вокруг осей сцены (X — вдоль ширины, Y — вверх, Z — вдоль длины)
const orientModel = (model) => {
	if (!viewState) return
	const deg = THREE.MathUtils.degToRad
	model.rotation.set(deg(viewState.rot_x), deg(viewState.rot_y), deg(viewState.rot_z))
}

// ── Связь с генератором: WebSocket, одна общая корзина на всех клиентов ──
// Сервер нумерует каждую правку любого клиента (version) и присылает модель вместе с x/y/z,
// только если она актуальна: устаревшие (пока считали, пришла новая правка) не приходят вовсе.
const connected = ref(false)
let socket = null
let stopped = false // компонент размонтирован — не переподключаемся
let pendingHeader = null // заголовок «model», ждущий своего бинарного кадра с GLB

// Мои правки: локальный номер (seq) и подтверждения сервера (ack приносит seq и присвоенную version)
const sentSeq = ref(0) // номер последней отправленной правки
const ackedSeq = ref(0) // номер последней правки, на которую пришёл ack
const lastAck = ref(0) // version, присвоенная моей последней подтверждённой правке
const shownVersion = ref(0) // версия модели на экране
const settledVersion = ref(0) // последняя версия, по которой пришёл итог: модель или ошибка


// Спиннер: ещё нет первой модели или ждём итог по своей правке
const isLoading = computed(
	() => connected.value && (shownVersion.value === 0 || hasUnacked() || lastAck.value > settledVersion.value),
)

// Журнал полученных моделей и ошибок (для отладки)
const events = ref([])
let nextEventId = 1
const errorCount = ref(0)
const reversedEvents = computed(() => [...events.value].reverse())

// Длительность правки: от отправки до итога (модель или ошибка). Известна только для моих правок:
// время отправки хранится по seq, ack связывает seq с version, итог приходит по version.
const sentAtBySeq = new Map() // seq → performance.now() в момент отправки
const startedByVersion = new Map() // version → performance.now() отправки правки, породившей эту версию

const formatDuration = (ms) => (ms < 1000 ? `${Math.round(ms)} мс` : `${(ms / 1000).toFixed(1)} с`)

// Все параметры модели из заголовка: имена берём из пределов (limits), пока их нет — из params.
// Сервер присылает все параметры (их значения читает из сцены Blender); проверка на undefined — только страховка.
const paramsText = (header) =>
	Object.keys(limits.value ?? params)
		.filter((k) => header[k] !== undefined)
		.map((k) => `${k}=${header[k]}`)
		.join(', ')

const durationOf = (version) => {
	const started = startedByVersion.get(version)
	if (started === undefined) return '—' // правка другого клиента: времени отправки у нас нет
	startedByVersion.delete(version)
	return formatDuration(performance.now() - started)
}

const logEvent = (entry) => {
	events.value.push({ id: nextEventId++, time: durationOf(entry.version), ...entry })
	if (events.value.length > MAX_HISTORY) events.value.splice(0, events.value.length - MAX_HISTORY)
}

const logError = (version, error) => {
	errorCount.value++
	logEvent({ version, status: 'error', error })
}

// Слои 3D-печати: процедурный рельеф в шейдере, без текстуры и UV.
// Высота «валика» слоя зависит только от мировой Y, поэтому полоски всегда горизонтальны.
// Геометрия не меняется — отклоняется только нормаль (как у bump-карты), рельеф «рисует» свет.
const layerUniforms = {
	uLayerHeight: { value: LAYER_HEIGHT },
	uLayerDepth: { value: LAYER_DEPTH },
	uLayerBase: { value: 0 }, // мировая Y дна модели: слои начинаются от стола
	uLayerFadeFrom: { value: LAYER_FADE_FROM_PX },
	uLayerFadeTo: { value: LAYER_FADE_TO_PX },
}

petgMaterial.onBeforeCompile = (shader) => {
	Object.assign(shader.uniforms, layerUniforms)

	shader.vertexShader = shader.vertexShader
		.replace('#include <common>', '#include <common>\nvarying float vLayerY;')
		.replace(
			'#include <project_vertex>',
			'#include <project_vertex>\nvLayerY = (modelMatrix * vec4(transformed, 1.0)).y;',
		)

	shader.fragmentShader = shader.fragmentShader
		.replace(
			'#include <common>',
			/* glsl */ `#include <common>
varying float vLayerY;
uniform float uLayerHeight;
uniform float uLayerDepth;
uniform float uLayerBase;
uniform float uLayerFadeFrom;
uniform float uLayerFadeTo;

// Наклон нормали от слоёв. Профиль валика — парабола h = depth·(1 − s²), s ∈ [−1, 1] внутри слоя:
// скруглённый гребень и острая бороздка, как у реального экструдированного валика.
vec3 applyPrintLayers(vec3 n) {
	float y = vLayerY - uLayerBase;
	// Сколько мировых единиц Y приходится на пиксель → сколько пикселей занимает слой
	float pxPerLayer = uLayerHeight / max(fwidth(y), 1e-6);
	float fade = smoothstep(uLayerFadeFrom, uLayerFadeTo, pxPerLayer);
	if (fade <= 0.0) return n;

	float s = 2.0 * fract(y / uLayerHeight) - 1.0;
	float dhdy = -4.0 * uLayerDepth * s / uLayerHeight; // производная h по мировой Y
	// Градиент высоты = dh/dy · «вверх»; проецируем на касательную плоскость и вычитаем из нормали
	vec3 upView = normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
	vec3 g = dhdy * fade * upView;
	return normalize(n - (g - n * dot(n, g)));
}`,
		)
		.replace(
			'#include <normal_fragment_maps>',
			'#include <normal_fragment_maps>\nnormal = applyPrintLayers(normal);',
		)
		.replace(
			'#include <clearcoat_normal_fragment_begin>',
			'#include <clearcoat_normal_fragment_begin>\n#ifdef USE_CLEARCOAT\nclearcoatNormal = applyPrintLayers(clearcoatNormal);\n#endif',
		)
}

// Применяет PETG ко всем мешам модели
const prepareModel = (model) => {
	model.traverse((o) => {
		if (o.isMesh) o.material = petgMaterial
	})
}

// Освобождает геометрии убираемой модели (материал общий — его не трогаем)
const disposeModel = (model) => {
	model.traverse((o) => {
		if (o.isMesh) o.geometry.dispose()
	})
}


const hasUnacked = () => sentSeq.value > ackedSeq.value

// Можно ли поставить пришедшие значения в слайдеры и поля.
// Модель показываем всегда (сервер прислал её как актуальную), а значения — не всегда:
// если я как раз тяну слайдер, пришедшая версия может быть старее моей правки, и слайдер «отдёрнет» назад.
// Применяем, когда все мои правки подтверждены (hasUnacked) и модель не старее последней из них (lastAck).
// Чужие более поздние правки имеют большую version и поэтому применяются.
const shouldApplyValues = (msg) => !hasUnacked() && msg.version >= lastAck.value

// Заменяет модель на столе
const showModel = async (header, buffer) => {
	let gltf
	try {
		gltf = await loader.parseAsync(buffer, '')
	} catch (e) {
		logError(header.version, `разбор GLB: ${e?.message || e}`)
		return
	}
	// Пока разбирали, могла показаться более новая модель
	if (header.version <= shownVersion.value) {
		disposeModel(gltf.scene)
		return
	}
	if (currentModel) {
		turntable.remove(currentModel)
		disposeModel(currentModel)
	}
	currentModel = gltf.scene
	prepareModel(currentModel)
	orientModel(currentModel)
	// Слои печати отсчитываются от дна модели (той же высоты, где стоит стол)
	layerUniforms.uLayerBase.value = placeBed(bed, currentModel)
	turntable.add(currentModel)

	shownVersion.value = header.version
	settledVersion.value = Math.max(settledVersion.value, header.version)
	if (shouldApplyValues(header)) {
		for (const key of Object.keys(params)) {
			if (header[key] !== undefined) params[key] = lastValid[key] = header[key]
		}
	}
	logEvent({ version: header.version, status: 'done', params: paramsText(header) })
}

const onMessage = (data) => {
	// Бинарный кадр — GLB к заголовку «model», который пришёл прямо перед ним
	if (data instanceof ArrayBuffer) {
		const header = pendingHeader
		pendingHeader = null
		if (header) showModel(header, data)
		return
	}
	const msg = JSON.parse(data)
	if (msg.type === 'ack') {
		ackedSeq.value = msg.seq
		lastAck.value = msg.version
		if (sentAtBySeq.has(msg.seq)) {
			startedByVersion.set(msg.version, sentAtBySeq.get(msg.seq))
			sentAtBySeq.delete(msg.seq)
		}
	} else if (msg.type === 'model') {
		pendingHeader = msg
	} else if (msg.type === 'view') {
		applyView(msg)
	} else if (msg.type === 'error') {
		settledVersion.value = Math.max(settledVersion.value, msg.version)
		logError(msg.version, msg.error)
	}
}

// Серверы gen на выбор: список адресов отдаёт сайт (/gens, из GEN_ORIGINS — свой в каждом окружении).
// Браузер подключается к выбранному gen напрямую (http→ws, https→wss): на странице по https адреса должны быть https
// (иначе mixed content); в dev первый адрес — http://localhost:8080. Выбор помнит браузер (localStorage), у каждого свой.
const GEN_STORAGE_KEY = 'gen'
const gens = ref([])
const genIndex = ref(0)
const genOrigin = () => gens.value[genIndex.value]
const wsUrl = () => genOrigin().replace(/^http/, 'ws') + '/ws'

const genLabel = (origin) => origin.replace(/^https?:\/\//, '')

const pause = () => new Promise((resolve) => setTimeout(resolve, RECONNECT_DELAY))

const loadGens = async () => {
	let list
	try {
		const res = await fetch('/gens', { cache: 'no-store' })
		if (!res.ok) return
		list = await res.json()
	} catch {
		return // сайт недоступен — попробуем снова
	}
	if (!list.length) return
	let saved = 0
	try { saved = Number(localStorage.getItem(GEN_STORAGE_KEY)) || 0 } catch { /* хранилище недоступно */ }
	gens.value = list
	genIndex.value = saved >= 0 && saved < list.length ? saved : 0
}

// Допустимые значения параметров ({x: {min, max}, …}) задаёт Blender-воркер; gen отдаёт их отдельным запросом.
// Своих копий диапазонов здесь нет: пока пределы не пришли, слайдеров нет. Адрес — выбранный gen, как у wsUrl.
const limits = ref(null)
let limitsRun = 0 // номер последнего запроса пределов: более ранние (в том числе от прежнего сервера) бросают свой цикл
const loadLimits = async () => {
	const run = ++limitsRun
	while (!stopped && run === limitsRun) {
		try {
			const res = await fetch(genOrigin() + '/limits', { cache: 'no-store' })
			if (run !== limitsRun) return
			if (res.ok) {
				limits.value = await res.json()
				return
			}
		} catch { /* gen недоступен — пробуем снова */ }
		await pause()
	}
}

// Номера версий прежнего соединения или сервера для нового ничего не значат
const forgetVersions = () => {
	lastAck.value = 0
	shownVersion.value = 0
	settledVersion.value = 0
}

// Переключение на другой gen: у него своя корзина и свои пределы, поэтому всё прежнее забываем.
// Закрытие сокета запускает обычное переподключение (onclose → connect), уже по новому адресу.
const selectGen = (index) => {
	index = Number(index)
	if (index === genIndex.value) return
	genIndex.value = index
	try { localStorage.setItem(GEN_STORAGE_KEY, String(index)) } catch { /* хранилище недоступно */ }
	limits.value = null
	limitsRun++ // отменяет запрос пределов прежнего сервера
	events.value = []
	errorCount.value = 0
	forgetVersions()
	pendingHeader = null
	socket?.close()
}

const connect = () => {
	if (stopped || socket) return
	socket = new WebSocket(wsUrl())
	socket.binaryType = 'arraybuffer'
	socket.onopen = () => {
		connected.value = true
		loadLimits() // gen мог перезапуститься с другими пределами: спрашиваем при каждом подключении
		// gen мог перезапуститься и начать нумерацию заново: забываем старые версии.
		// Сразу после подключения сервер пришлёт текущую корзину.
		ackedSeq.value = sentSeq.value
		sentAtBySeq.clear()
		startedByVersion.clear()
		forgetVersions()
	}
	socket.onmessage = (e) => onMessage(e.data)
	// Обрыв или неудачное подключение (gen ещё прогревает Blender) — пробуем снова
	socket.onclose = () => {
		connected.value = false
		pendingHeader = null
		ackedSeq.value = sentSeq.value // подтверждения по этому соединению уже не придут
		socket = null
		if (!stopped) setTimeout(connect, RECONNECT_DELAY)
	}
}

// Применение правки поля. Событие change приходит при уходе из поля (потеря фокуса) или Enter, а не при наборе каждой цифры.
// Число подгоняем под пределы и шаг, не число — возвращаем прежнее значение
const commitNumber = (c) => {
	const lim = limits.value?.[c.key]
	const v = params[c.key]
	if (!lim || typeof v !== 'number' || !Number.isFinite(v)) {
		params[c.key] = lastValid[c.key]
		return
	}
	const decimals = (String(c.step).split('.')[1] || '').length
	const snapped = Math.round((v - lim.min) / c.step) * c.step + lim.min
	const fixed = Number(Math.min(lim.max, Math.max(lim.min, snapped)).toFixed(decimals))
	const changed = fixed !== lastValid[c.key]
	params[c.key] = lastValid[c.key] = fixed
	if (changed) sendParams()
}

// Кнопки «−» и «+» по бокам поля: сдвиг на шаг, дальше — как после ввода (пределы, шаг, отправка)
const stepNumber = (c, dir) => {
	const v = typeof params[c.key] === 'number' ? params[c.key] : lastValid[c.key]
	params[c.key] = v + dir * c.step
	commitNumber(c)
}

// Отправляет правку слайдера в общую корзину
const sendParams = () => {
	// Нет связи — правка остаётся локальной; после переподключения придёт общая корзина
	if (socket?.readyState !== WebSocket.OPEN) return
	sentSeq.value++
	sentAtBySeq.set(sentSeq.value, performance.now())
	// Параметры без значения (первая модель ещё не пришла) не отправляем
	const known = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null))
	socket.send(JSON.stringify({ type: 'params', ...known, seq: sentSeq.value }))
}

// Возврат к исходному виду: камера, точка обзора и поворот столика с моделью
const resetView = () => {
	controls?.reset()
	turntable?.rotation.set(0, 0, 0)
}

// Полноэкранный режим области 3D-модели.
// Основной путь — Fullscreen API (как F11: скрывает адресную строку и заголовок браузера).
// Где его нет (iPhone) или браузер отказал — «псевдо-полноэкранный»: область растягивается на всё окно через CSS.
const viewerArea = ref(null)
const isFullscreen = ref(false)
const fullscreenTitle = computed(() => (isFullscreen.value ? 'Выйти из полноэкранного режима' : 'Во весь экран'))
let pseudoFullscreen = false

const fullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement

const setPseudoFullscreen = (on) => {
	pseudoFullscreen = on
	document.documentElement.style.overflow = on ? 'hidden' : '' // страница под областью не прокручивается
	syncFullscreen()
}

const syncFullscreen = () => {
	isFullscreen.value = pseudoFullscreen || (!!viewerArea.value && fullscreenElement() === viewerArea.value)
}

const toggleFullscreen = async () => {
	if (isFullscreen.value) {
		if (pseudoFullscreen) return setPseudoFullscreen(false)
		const exit = document.exitFullscreen || document.webkitExitFullscreen
		return exit?.call(document)
	}
	const el = viewerArea.value
	const request = el.requestFullscreen || el.webkitRequestFullscreen
	if (!request) return setPseudoFullscreen(true)
	try {
		await request.call(el)
	} catch {
		setPseudoFullscreen(true)
	}
}

// Esc выходит из псевдо-режима так же, как из настоящего полноэкранного
const onKeydown = (e) => {
	if (e.key === 'Escape' && pseudoFullscreen) setPseudoFullscreen(false)
}

// «Комната» для освещения: равнопромежуточная (equirectangular) картинка, нарисованная на canvas один раз.
// Тёмные стены с градиентом, холодное яркое «окно» справа спереди и тёплая «лампа» слева сзади —
// из-за неравномерности яркости свет выглядит естественно, а на глянцевом PETG видны отражения.
// Положение на картинке: x — азимут (0.5 = направление +X, 0.75 = +Z), y — высота (0 = потолок, 0.5 = горизонт).
// walls — градиент стен сверху вниз: [позиция 0..1, цвет].

const makeRoomEnvironment = (walls = ROOM_WALLS_LIGHTING) => {
	const W = 1024
	const H = 512
	const canvas = document.createElement('canvas')
	canvas.width = W
	canvas.height = H
	const ctx = canvas.getContext('2d')

	// Стены, потолок и пол
	const bg = ctx.createLinearGradient(0, 0, 0, H)
	for (const [pos, color] of walls) bg.addColorStop(pos, color)
	ctx.fillStyle = bg
	ctx.fillRect(0, 0, W, H)

	// Пятно света на стене и источники: свечение через shadowBlur даёт мягкие края
	const glow = (cx, cy, w, h, color, blur) => {
		ctx.save()
		ctx.shadowColor = color
		ctx.shadowBlur = blur
		ctx.fillStyle = color
		ctx.fillRect(cx - w / 2, cy - h / 2, w, h)
		ctx.restore()
	}
	glow(0.49 * W, 0.33 * H, 140, 90, '#dfeaff', 60) // окно: холодный дневной свет
	glow(0.11 * W, 0.44 * H, 40, 40, '#ffb36b', 50) // лампа: тёплый свет

	const texture = new THREE.CanvasTexture(canvas)
	texture.mapping = THREE.EquirectangularReflectionMapping
	texture.colorSpace = THREE.SRGBColorSpace
	return texture
}

// Прозрачный стол 3D-принтера: стеклянная пластина с сеткой в цветах интерфейса.
// Прозрачный и не пишет глубину — сквозь него видно дно корзины, если посмотреть снизу.

const makeBedTexture = (renderer) => {
	const S = 64 // одна клетка; текстура повторяется по пластине
	const canvas = document.createElement('canvas')
	canvas.width = canvas.height = S
	const ctx = canvas.getContext('2d')
	ctx.fillStyle = 'rgba(148, 197, 255, 0.10)' // лёгкая голубая «стеклянная» заливка
	ctx.fillRect(0, 0, S, S)
	ctx.fillStyle = 'rgba(125, 211, 252, 0.1)' // линии сетки — акцентный цвет UI
	ctx.fillRect(0, 0, S, 2)
	ctx.fillRect(0, 0, 2, S)
	const texture = new THREE.CanvasTexture(canvas)
	texture.wrapS = texture.wrapT = THREE.RepeatWrapping
	texture.colorSpace = THREE.SRGBColorSpace
	texture.anisotropy = renderer.capabilities.getMaxAnisotropy() // сетка не мылится под углом
	return texture
}

const createBed = (renderer) => {
	const geometry = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2)
	const material = new THREE.MeshStandardMaterial({
		map: makeBedTexture(renderer),
		transparent: true,
		depthWrite: false,
		side: THREE.DoubleSide,
		metalness: 0,
		roughness: 0.15, // гладкое стекло: на нём отражается «окно» комнаты
	})
	const bed = new THREE.Mesh(geometry, material)
	// Рамка по краю пластины
	const edges = new THREE.LineSegments(
		new THREE.EdgesGeometry(geometry),
		new THREE.LineBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.6 }),
	)
	bed.add(edges)
	bed.renderOrder = 1
	bed.scale.set(BED_SIZE, 1, BED_SIZE)
	// Сетка выровнена по центру стола: линия проходит через центр, неполные клетки — по краям
	const cells = BED_SIZE / BED_CELL
	material.map.repeat.set(cells, cells)
	const shift = -((cells / 2) % 1)
	material.map.offset.set(shift, shift)
	bed.visible = false // покажем, когда придёт первая модель
	return bed
}

// Ставит стол под нижнюю точку модели (размер стола фиксирован, меняется только высота)
const placeBed = (bed, model) => {
	const box = new THREE.Box3().setFromObject(model)
	const gap = Math.max((box.max.y - box.min.y) * 0.002, 0.01) // зазор от z-fighting с дном
	bed.position.set(0, box.min.y - gap, 0)
	bed.visible = true
	return box.min.y
}

onBeforeUnmount(() => {
	stopped = true
	socket?.close()
	resizeObserver?.disconnect()
	controls?.dispose()
	document.removeEventListener('fullscreenchange', syncFullscreen)
	document.removeEventListener('webkitfullscreenchange', syncFullscreen)
	document.removeEventListener('keydown', onKeydown)
	if (pseudoFullscreen) setPseudoFullscreen(false)
})

onMounted(() => {
	// Состояние кнопки следим по событию: из полноэкранного режима можно выйти и по Esc/жесту браузера
	document.addEventListener('fullscreenchange', syncFullscreen)
	document.addEventListener('webkitfullscreenchange', syncFullscreen)
	document.addEventListener('keydown', onKeydown)

	const width = canvasContainer.value.clientWidth
	const height = canvasContainer.value.clientHeight

	scene = new THREE.Scene()

	camera = new THREE.PerspectiveCamera(15, width / height, 0.1, 10000)
	camera.position.set(460, 460, 1125) // то же направление, расстояние ≈ 1300 мм (как distance по умолчанию)
	camera.lookAt(0, 0, 0)

	renderer = new THREE.WebGLRenderer({ antialias: true })
	// Чёткость на экранах с высокой плотностью, но не больше 2x (экономим GPU на планшетах)
	renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO))
	renderer.setSize(width, height)
	// Тон-маппинг: без пересветов, плавные переходы как у камеры
	renderer.toneMapping = THREE.ACESFilmicToneMapping
	renderer.toneMappingExposure = TONE_MAPPING_EXPOSURE
	canvasContainer.value.appendChild(renderer.domElement)

	// Управление видом: колесо/щипок — масштаб, ЛКМ/один палец — перемещение,
	// два пальца — вращение; ПКМ — вращение мышью
	controls = new OrbitControls(camera, renderer.domElement)
	controls.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }
	controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_ROTATE }
	controls.enableDamping = true
	controls.target.set(0, 0, 0)
	controls.saveState() // исходный вид, к которому вернёт resetView

	// Базовое освещение сцены
	// Тёплый основной свет: цветовой контраст с холодным «окном» делает освещение живее
	light = new THREE.DirectionalLight(LIGHT_COLOR, LIGHT_INTENSITY)
	// Свет и окружение закреплены в мире (как в комнате): при вращении вида освещение на модели меняется.
	// Начальное положение света считаем относительно стартовой камеры, затем переводим в мировые координаты.
	camera.updateMatrixWorld()
	const targetLocal = camera.worldToLocal(new THREE.Vector3(0, 0, 0))
	// Свет падает почти от зрителя: луч отклонён от направления взгляда на LIGHT_ANGLE
	// вправо и вверх (диагональ 45°). Меняйте LIGHT_ANGLE, чтобы сделать свет косее или прямее.
	const dist = targetLocal.length()
	const side = dist * Math.sin(LIGHT_ANGLE) * Math.SQRT1_2
	light.position.copy(camera.localToWorld(new THREE.Vector3(side, side, targetLocal.z + dist * Math.cos(LIGHT_ANGLE))))
	light.target.position.set(0, 0, 0)
	scene.add(light, light.target)

	// Карта окружения (IBL) — нарисованная «комната». Строится один раз, на кадры почти не влияет.
	scene.environment = makeRoomEnvironment()
	scene.environmentIntensity = ENVIRONMENT_INTENSITY

	// Фон — та же комната (окно и лампа на тех же местах), но в цветах карточек и размытая.
	// Освещение при этом не меняется: для него используется нейтральная комната выше.
	scene.background = makeRoomEnvironment(ROOM_WALLS_BACKGROUND)
	scene.backgroundBlurriness = 0.5
	scene.backgroundIntensity = 0.75

	// Поворотный столик: стол принтера и модель вращаются вместе
	turntable = new THREE.Group()
	scene.add(turntable)
	// Прозрачный стол принтера под моделью
	bed = createBed(renderer)
	turntable.add(bed)

	// Цикл рендеринга с медленным вращением для наглядности
	const animate = () => {
		requestAnimationFrame(animate)
		turntable.rotation.y += rotationSpeed
		controls.update()
		renderer.render(scene, camera)
	}
	animate()

	// Подстройка рендерера и камеры под размер контейнера
	const resize = () => {
		const w = canvasContainer.value.clientWidth
		const h = canvasContainer.value.clientHeight
		if (!w || !h) return
		camera.aspect = w / h
		camera.updateProjectionMatrix()
		renderer.setSize(w, h)
	}
	resizeObserver = new ResizeObserver(resize)
	resizeObserver.observe(canvasContainer.value)

	// Подключение к генератору: сразу придёт текущая общая корзина
	// сначала список серверов: от него зависит адрес подключения
	;(async () => {
		while (!stopped && !gens.value.length) {
			await loadGens()
			if (!gens.value.length) await pause()
		}
		connect()
	})()
})
</script>

<template>
	<div class="layout">
		<div class="viewer-wrapper">

			<div class="controls">
				<template v-if="limits">
					<label v-for="key in TOP_SLIDERS" :key="key" class="slider">
						<span>{{ key.toUpperCase() }}</span>
						<input v-model.number="params[key]" type="range" :min="limits[key].min" :max="limits[key].max" step="1" @input="sendParams" />
						<output>{{ params[key] }}</output>
					</label>
					<template v-for="c in NUMBER_CONTROLS" :key="c.key">
						<div v-if="params[c.key] !== null" class="num" :title="c.title">
							<button
								type="button"
								class="num-btn"
								:aria-label="c.title + ': меньше'"
								:disabled="!(params[c.key] > limits[c.key].min)"
								@click="stepNumber(c, -1)"
							>−</button>
							<input
								v-model.number="params[c.key]"
								type="number"
								:min="limits[c.key].min"
								:max="limits[c.key].max"
								:step="c.step"
								:aria-label="c.title"
								@change="commitNumber(c)"
							/>
							<button
								type="button"
								class="num-btn"
								:aria-label="c.title + ': больше'"
								:disabled="!(params[c.key] < limits[c.key].max)"
								@click="stepNumber(c, 1)"
							>+</button>
						</div>
					</template>
				</template>
			</div>

			<div class="viewer-row">
				<label v-if="limits" class="slider-z">
					<span>Z</span>
					<input v-model.number="params.z" type="range" :min="limits.z.min" :max="limits.z.max" step="1" class="vertical" @input="sendParams" />
					<output>{{ params.z }}</output>
				</label>

				<div ref="viewerArea" class="canvas-wrap" :class="{ 'is-fullscreen': isFullscreen }">
					<div ref="canvasContainer" class="canvas-box"></div>
					<div class="overlay" :class="{ active: isLoading || !connected }">
						<div class="spinner"></div>
						<div v-if="!connected">Подключение к генератору…</div>
					</div>
					<button
						type="button"
						class="view-btn fullscreen-toggle"
						:title="fullscreenTitle"
						:aria-label="fullscreenTitle"
						@click="toggleFullscreen"
					>
						<!-- Развернуть: уголки наружу; свернуть: уголки внутрь -->
						<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
							<path :d="isFullscreen ? 'M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5' : 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5'" />
						</svg>
					</button>
					<button type="button" class="view-btn reset-view" title="Вернуть исходный вид" aria-label="Вернуть исходный вид" @click="resetView">⟲</button>
				</div>
			</div>
		</div>

		<aside class="queue-panel">

			<div class="queue-summary">
				<span v-if="errorCount" class="stat error">Ошибок: {{ errorCount }}</span>
			</div>

			<ul class="queue-list">
				<li v-for="ev in reversedEvents" :key="ev.id" class="job" :class="ev.status">
					<div class="job-row">
						<span class="job-params">{{ ev.params }}</span>
						<span class="job-duration">{{ ev.time }}</span>
						<span v-if="ev.status !== 'done'" class="badge" :class="ev.status">{{ ev.status }}</span>
					</div>
					<div v-if="ev.error" class="job-error">{{ ev.error }}</div>
				</li>
				<li v-if="!events.length" class="empty">Моделей пока нет</li>
			</ul>

			<label v-if="gens.length > 1" class="gen-select">
				<select :value="genIndex" @change="selectGen($event.target.value)">
					<option v-for="(origin, i) in gens" :key="origin" :value="i">{{ genLabel(origin) }}</option>
				</select>
			</label>
		</aside>
	</div>
</template>

<style scoped>
.gen-select {
	display: flex;
	flex-direction: column;
	gap: 4px;
	margin-top: 8px;
	font-size: 12px;
	color: #9fb3d9;
}
.gen-select select {
	width: 100%;
	box-sizing: border-box;
	padding: 4px 6px;
	font: inherit;
	color: inherit;
	background: rgba(255, 255, 255, 0.06);
	border: 1px solid rgba(255, 255, 255, 0.15);
	border-radius: 6px;
}
.gen-select option {
	color: #000;
}
.layout {
	display: flex;
	align-items: stretch;
	gap: 24px;
	width: 100%;
}
/* Общий вид двух карточек: вьювер и журнал */
.queue-panel,
.viewer-wrapper {
	box-sizing: border-box;
	border-radius: 20px;
	background: linear-gradient(160deg, #1e2d44 0%, #16212f 100%);
	border: 1px solid rgba(148, 197, 255, 0.15);
	box-shadow: 0 30px 60px -20px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(0, 0, 0, 0.3);
	color: #e6edf6;
}
/* Общая подложка блоков с ползунками */
.controls,
label.slider-z {
	border-radius: 14px;
	background: rgba(255, 255, 255, 0.04);
	border: 1px solid rgba(255, 255, 255, 0.08);
}
.queue-panel {
	display: flex;
	flex-direction: column;
	gap: 12px;
	flex: 0 0 clamp(240px, 24vw, 320px);
	min-height: 0;
	contain: size; /* высоту задаёт вьювер, а не длина списка */
	padding: 24px 18px;
}
.queue-summary {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	font-size: 0.75rem;
}
.stat {
	padding: 3px 8px;
	border-radius: 999px;
	background: rgba(255, 255, 255, 0.06);
}
.stat.error { color: #f87171; }
.queue-list {
	flex: 1;
	min-height: 0;
	margin: 0;
	padding: 0;
	overflow-y: auto;
	list-style: none;
	display: flex;
	flex-direction: column;
	gap: 8px;
}
.job {
	padding: 8px 10px;
	border-radius: 10px;
	background: rgba(255, 255, 255, 0.04);
	border: 1px solid rgba(255, 255, 255, 0.08);
	border-left-width: 3px;
	font-size: 0.8rem;
}
.job.done { border-left-color: #34d399; }
.job.error { border-left-color: #f87171; }
.job-row {
	display: flex;
	align-items: center;
	gap: 8px;
}
.job-params {
	flex: 1;
	font-variant-numeric: tabular-nums;
}
.badge {
	padding: 2px 8px;
	border-radius: 999px;
	font-size: 0.7rem;
	font-weight: 600;
	background: rgba(255, 255, 255, 0.08);
}
.badge.error { color: #f87171; }
.job-duration {
	font-size: 0.7rem;
	color: #94a9c4;
	font-variant-numeric: tabular-nums;
	white-space: nowrap;
}
.job-error {
	margin-top: 4px;
	font-size: 0.7rem;
	color: #f87171;
	word-break: break-word;
}
.empty {
	color: #94a9c4;
	font-size: 0.8rem;
	text-align: center;
	padding: 12px 0;
}
.viewer-wrapper {
	display: flex;
	flex-direction: column;
	gap: 16px;
	flex: 1 1 0;
	min-width: 0;
	padding: clamp(12px, 2vw, 24px);
}
.controls {
	display: flex;
	flex-direction: row; /* X и Y в одной строке */
	gap: 16px;
	align-items: center;
	padding: 12px 14px;
}
label {
	display: flex;
	align-items: center;
	gap: 8px;
	font-size: 0.85rem;
	font-weight: 600;
	color: #94a9c4;
}
label span {
	display: inline-flex;
	width: 24px;
	height: 24px;
	align-items: center;
	justify-content: center;
	border-radius: 6px;
	background: rgba(125, 211, 252, 0.12);
	color: #7dd3fc;
}
label.slider {
	flex: 1 1 0; /* X и Y делят строку поровну */
	min-width: 0;
}
input[type='range'] {
	flex: 1;
	min-width: 0;
	padding: 0;
	border: none;
	background: transparent;
	accent-color: #7dd3fc;
	cursor: pointer;
}
input[type='range']:focus {
	box-shadow: none;
}
.num {
	display: flex;
	align-items: center;
	gap: 4px;
	flex: 0 0 auto;
}
.num input {
	width: 56px;
	text-align: center;
	appearance: textfield; /* родные стрелки справа скрыты: вместо них кнопки по бокам */
	-moz-appearance: textfield;
}
.num input::-webkit-inner-spin-button,
.num input::-webkit-outer-spin-button {
	-webkit-appearance: none;
	margin: 0;
}
.num-btn {
	width: 28px;
	height: 32px;
	padding: 0;
	border-radius: 8px;
	background: rgba(255, 255, 255, 0.08);
	box-shadow: none;
	color: #7dd3fc;
	font-size: 1.1rem;
	line-height: 1;
}
.num-btn:hover:not(:disabled) {
	background: rgba(125, 211, 252, 0.2);
	filter: none;
	transform: none;
}
.num-btn:active:not(:disabled) {
	transform: none;
}
.num-btn:disabled {
	opacity: 0.35;
	cursor: default;
}
output {
	width: 3ch;
	text-align: right;
	color: #e6edf6;
	font-variant-numeric: tabular-nums;
}
input {
	width: 70px;
	padding: 6px 8px;
	border-radius: 8px;
	border: 1px solid rgba(255, 255, 255, 0.12);
	background: #0f1724;
	color: #e6edf6;
	font: inherit;
	outline: none;
	transition: border-color 0.15s, box-shadow 0.15s;
}
input:focus {
	border-color: #7dd3fc;
	box-shadow: 0 0 0 3px rgba(125, 211, 252, 0.2);
}
button {
	padding: 8px 18px;
	border: none;
	border-radius: 10px;
	background: linear-gradient(90deg, #3b82f6, #8b5cf6);
	color: #fff;
	font: inherit;
	font-weight: 600;
	cursor: pointer;
	box-shadow: 0 6px 16px -4px rgba(99, 102, 241, 0.6);
	transition: transform 0.15s, filter 0.15s;
}
button:hover {
	filter: brightness(1.12);
	transform: translateY(-1px);
}
button:active {
	transform: translateY(0);
}
.canvas-box {
	width: 100%;
	height: clamp(280px, 70vh, 800px);
	overflow: hidden;
	border-radius: 14px;
	border: 1px solid rgba(125, 211, 252, 0.25);
	box-shadow: inset 0 0 40px rgba(0, 0, 0, 0.5);
}
.canvas-wrap {
	position: relative;
	flex: 1;
	min-width: 0;
}
.viewer-row {
	display: flex;
	align-items: stretch;
	gap: 12px;
}
label.slider-z {
	flex-direction: column;
	width: auto;
	padding: 12px 6px;
}
input[type='range'].vertical {
	writing-mode: vertical-lr;
	direction: rtl; /* большее значение сверху */
	width: auto;
	height: 100%;
}
label.slider-z output {
	text-align: center;
}
/* Полноэкранный режим: и для Fullscreen API, и для псевдо-режима (position: fixed на всё окно) */
.canvas-wrap.is-fullscreen {
	position: fixed;
	inset: 0;
	z-index: 1000;
	width: 100%;
	height: 100%;
	background: #0f1724;
}
.canvas-wrap.is-fullscreen .canvas-box {
	height: 100%;
	border: none;
	border-radius: 0;
}
.canvas-wrap.is-fullscreen .overlay {
	border-radius: 0;
}
/* Кнопки поверх 3D-вида */
button.view-btn {
	position: absolute;
	top: 10px;
	z-index: 2;
	width: 36px;
	height: 36px;
	padding: 0;
	display: inline-flex;
	align-items: center;
	justify-content: center;
	font-size: 1.25rem;
	line-height: 1;
	border-radius: 10px;
	background: rgba(15, 23, 36, 0.75);
	border: 1px solid rgba(125, 211, 252, 0.35);
	color: #7dd3fc;
	box-shadow: none;
}
button.reset-view {
	right: 10px;
}
button.fullscreen-toggle {
	left: 10px;
}
.overlay {
	position: absolute;
	inset: 0;
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	gap: 12px;
	border-radius: 14px;
	background: rgba(8, 12, 20, 0.7);
	backdrop-filter: blur(2px);
	color: #7dd3fc;
	font-size: 0.9rem;
	opacity: 0;
	pointer-events: none;
	transition: opacity 0.3s;
}
.overlay.active {
	opacity: 1;
	pointer-events: auto;
}
.spinner {
	width: 44px;
	height: 44px;
	border-radius: 50%;
	border: 4px solid rgba(125, 211, 252, 0.2);
	border-top-color: #7dd3fc;
	animation: spin 0.9s linear infinite;
}
@keyframes spin {
	to { transform: rotate(360deg); }
}
.canvas-box :deep(canvas) {
	display: block;
}
@media (max-width: 900px) {
	.layout {
		flex-direction: column;
	}
	.queue-panel {
		width: auto;
		height: 280px;
		flex: none;
	}
}
</style>
