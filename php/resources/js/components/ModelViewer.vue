<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

// ── Константы ──

// Журнал полученных моделей
const MAX_HISTORY = 15

// Скорость автовращения: радиан за кадр (при 60 FPS ≈ 3,4°/с); знак — направление
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
// Один общий материал на все меши модели; здесь начальный цвет (потом его задаёт gen) и шероховатость.
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
// Тёмно-синие тона интерфейса (#1e2d44 → #16212f) — для видимого фона
const ROOM_WALLS_BACKGROUND = [
	[0, '#34496a'], // потолок
	[0.35, '#263753'],
	[0.5, '#1e2d44'], // горизонт
	[0.7, '#16212f'],
	[1, '#0e1520'], // пол
]

const canvasContainer = ref(null)
// Параметры модели (имена — как в PARAMS у gen/worker.py).
// null — значение неизвестно, пока gen не пришлёт первую модель (оно берётся из сцены Blender).
const params = reactive({ x: null, y: null, z: null, thickness: null, excavation_height: null, perforation: null })
// Допустимые значения параметров ({x: {min, max}, …}): задаёт Blender-воркер, gen присылает их вместе с моделью.
// Элементы управления появляются с первой моделью.
const limits = ref(null)
// Слайдеры длины и ширины; слайдер Z — отдельный, вертикальный
const TOP_SLIDERS = ['x', 'y']
// Остальные слайдеры.
// geometric — шкала геометрическая: чем правее, тем быстрее растёт значение;
// decimals — сколько знаков после запятой у значения на такой шкале
const NUMBER_CONTROLS = [
	{ key: 'thickness', geometric: true, decimals: 1 },
	{ key: 'excavation_height' },
	{ key: 'perforation', geometric: true, decimals: 0 },
]
// Число положений кружка на геометрической шкале
const GEOMETRIC_STEPS = 330
// Названия слайдеров: подписи над ними и надпись на 3D-виде, пока слайдер двигают
const SLIDER_NAMES = {
	x: 'Длина',
	y: 'Ширина',
	z: 'Высота',
	thickness: 'Стенка',
	excavation_height: 'Выемка',
	perforation: 'Перфорация',
}
// Параметр слайдера, который сейчас держат мышью или пальцем; null — никакой
const dragged = ref(null)
const stopDrag = () => (dragged.value = null)

// turntable — «поворотный столик»: в нём стол принтера и модель, автовращение крутит его целиком
let scene, camera, renderer, controls, turntable, bed, currentModel, light, resizeObserver
// gen присылает модель в сжатии Draco; декодер лежит у нас в public_html/draco/ (копия из three)
const dracoLoader = new DRACOLoader().setDecoderPath('/draco/')
const loader = new GLTFLoader().setDRACOLoader(dracoLoader)

// Общий вид (цвет, камера, ориентация, свет): приходит от gen сообщением «view».
// Здесь последнее полученное — новой модели сразу выставляется текущая ориентация.
let viewState = null
let rotationSpeed = ROTATION_SPEED // автовращение, радиан за кадр; меняется сообщением «view»
let distanceVersion = null // distance_version, чьё расстояние уже выставлено камере; null — ещё никакое

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
	// Камера: то же направление на цель, другая дистанция. Только если расстояние задали заново,
	// иначе правка цвета и т. п. отменяла бы масштаб, выбранный пользователем.
	const version = v.distance_version ?? v.version
	if (version !== distanceVersion) {
		distanceVersion = version
		const offset = camera.position.clone().sub(controls.target)
		camera.position.copy(controls.target).addScaledVector(offset.normalize(), v.distance)
		controls.update()
	}
}

// Ориентация модели: повороты вокруг осей сцены (X — вдоль длины, Y — вверх, Z — вдоль ширины)
const orientModel = (model) => {
	if (!viewState) return
	const deg = THREE.MathUtils.degToRad
	model.rotation.set(deg(viewState.rot_x), deg(viewState.rot_y), deg(viewState.rot_z))
}

// ── Связь с генератором: WebSocket, одна общая корзина на всех клиентов ──
// Сервер нумерует каждую правку любого клиента (version) и присылает модель вместе с параметрами,
// только если она актуальна.
const connected = ref(false)
// Выбран другой gen, и от него ещё нет ответа: на экране значения прежнего, поэтому их нельзя править.
// Снимается моделью, ошибкой или неудачным подключением
const switching = ref(false)
let socket = null
let stopped = false // компонент размонтирован
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

// Длительность правки: от отправки до итога (модель или ошибка). Считается для моих правок:
// время отправки хранится по seq, ack связывает seq с version, итог приходит по version.
const sentAtBySeq = new Map() // seq → performance.now() в момент отправки
const startedByVersion = new Map() // version → performance.now() отправки правки, породившей эту версию

const formatDuration = (ms) => (ms < 1000 ? `${Math.round(ms)} мс` : `${(ms / 1000).toFixed(1)} с`)

// Размер GLB в килобайтах (1 КБ = 1024 байта), целым числом
const formatSize = (bytes) => `${Math.round(bytes / 1024)} КБ`

// Короткая запись параметров для строки журнала: «x×y×z · остальные в порядке NUMBER_CONTROLS»
const paramsShort = (header) =>
	[['x', 'y', 'z'].map((k) => header[k]).join('×'), ...NUMBER_CONTROLS.map((c) => header[c.key])].join(' · ')

// Все параметры модели из заголовка с именами (всплывающая подсказка): имена берём из пределов (limits), пока их нет — из params.
const paramsText = (header) =>
	Object.keys(limits.value ?? params)
		.filter((k) => header[k] !== undefined)
		.map((k) => `${k}=${header[k]}`)
		.join(', ')

const durationOf = (version) => {
	const started = startedByVersion.get(version)
	if (started === undefined) return '—' // правка другого клиента
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

// Слои 3D-печати: процедурный рельеф в шейдере.
// Высота «валика» слоя зависит только от мировой Y, поэтому полоски всегда горизонтальны.
// Отклоняется только нормаль (как у bump-карты), рельеф «рисует» свет.
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
		if (!o.isMesh) return
		// gen может присылать модель без нормалей (COMPACT_GLB в worker.py) — тогда затеняем грани плоско
		const flat = !o.geometry.attributes.normal
		if (petgMaterial.flatShading !== flat) {
			petgMaterial.flatShading = flat
			petgMaterial.needsUpdate = true // шейдер нужно пересобрать
		}
		o.material = petgMaterial
	})
}

// Освобождает геометрии убираемой модели (материал общий)
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
	const source = socket // соединение, по которому пришла модель
	const size = formatSize(buffer.byteLength) // до разбора: загрузчик получает сам буфер
	let gltf, parseError
	try {
		gltf = await loader.parseAsync(buffer, '')
	} catch (e) {
		parseError = e
	}
	// Пока разбирали, соединение сменилось (выбран другой gen или обрыв): эта модель уже не нужна
	if (source !== socket) {
		if (gltf) disposeModel(gltf.scene)
		return
	}
	switching.value = false
	if (!gltf) {
		logError(header.version, `разбор GLB: ${parseError?.message || parseError}`)
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
	limits.value = header.limits
	if (shouldApplyValues(header)) {
		for (const key of Object.keys(params)) {
			if (header[key] !== undefined) params[key] = header[key]
		}
	}
	logEvent({ version: header.version, status: 'done', params: paramsShort(header), title: paramsText(header), size })
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
		switching.value = false
		settledVersion.value = Math.max(settledVersion.value, msg.version)
		logError(msg.version, msg.error)
	}
}

// Серверы gen на выбор: список адресов отдаёт сайт (/gens, из GEN_ORIGINS — свой в каждом окружении).
// Браузер подключается к выбранному gen напрямую (http→ws, https→wss): на странице по https адреса должны быть https
// (иначе mixed content). Выбор помнит браузер (localStorage), у каждого свой.
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

// Забывает текущее соединение и закрывает его: всё, что ещё придёт по нему, не принимается
const dropSocket = () => {
	const old = socket
	socket = null
	connected.value = false
	pendingHeader = null
	ackedSeq.value = sentSeq.value // подтверждения по этому соединению уже не придут
	old?.close()
}

// Переключение на другой gen: у него своя корзина. Значения и пределы прежнего остаются на экране,
// пока не придут новые; журнал очищаем и сразу подключаемся по новому адресу.
const selectGen = (index) => {
	index = Number(index)
	if (index === genIndex.value) return
	genIndex.value = index
	try { localStorage.setItem(GEN_STORAGE_KEY, String(index)) } catch { /* хранилище недоступно */ }
	switching.value = true
	events.value = []
	errorCount.value = 0
	distanceVersion = null // у другого gen своя нумерация: берём его расстояние
	dropSocket()
	connect()
}

const connect = () => {
	if (stopped || socket) return
	const ws = new WebSocket(wsUrl())
	socket = ws
	ws.binaryType = 'arraybuffer'
	// События отброшенного соединения (ws !== socket) пропускаем
	ws.onopen = () => {
		if (ws !== socket) return
		connected.value = true
		// gen мог перезапуститься и начать нумерацию заново (а у другого gen она своя): забываем старые версии.
		// Сразу после подключения сервер пришлёт текущую корзину.
		ackedSeq.value = sentSeq.value
		sentAtBySeq.clear()
		startedByVersion.clear()
		lastAck.value = 0
		shownVersion.value = 0
		settledVersion.value = 0
	}
	ws.onmessage = (e) => {
		if (ws === socket) onMessage(e.data)
	}
	// Обрыв или неудачное подключение (gen ещё прогревает Blender) — пробуем снова
	ws.onclose = () => {
		if (ws !== socket) return
		dropSocket()
		switching.value = false // gen не отвечает: поля снова доступны
		if (!stopped) setTimeout(connect, RECONNECT_DELAY)
	}
}

// Правый край слайдера. Выемка не может быть выше корзины, поэтому её край — текущий Z
const sliderMax = (key) => (key === 'excavation_height' ? params.z : limits.value[key].max)

// Положение кружка слайдера от 0 до 1: там же рисуется число
const sliderPos = (key, geometric) => {
	const { min } = limits.value[key]
	const max = sliderMax(key)
	const pos = geometric ? Math.log(params[key] / min) / Math.log(max / min) : (params[key] - min) / (max - min)
	// Другой клиент мог задать выемку больше Z — кружок остаётся на краю
	return Math.min(pos, 1)
}

// Движение слайдера (кроме Z и геометрического)
const setValue = (key, value) => {
	params[key] = Number(value)
	sendParams()
}

// Движение слайдера Z: выемку, оказавшуюся выше, опускаем вместе с ним
const changeZ = () => {
	if (params.excavation_height > params.z) params.excavation_height = params.z
	sendParams()
}

// Движение слайдера с геометрической шкалой: номер положения → значение с точностью decimals знаков
const setGeometric = ({ key, decimals }, step) => {
	const { min, max } = limits.value[key]
	const factor = 10 ** decimals
	const value = Math.round(min * (max / min) ** (step / GEOMETRIC_STEPS) * factor) / factor
	// Соседние положения могут давать одно и то же значение — тогда отправлять нечего
	if (value === params[key]) return
	params[key] = value
	sendParams()
}

// Отправляет правку слайдера в общую корзину
const sendParams = () => {
	// Нет связи — правка остаётся локальной; после переподключения придёт общая корзина
	if (socket?.readyState !== WebSocket.OPEN) return
	sentSeq.value++
	sentAtBySeq.set(sentSeq.value, performance.now())
	socket.send(JSON.stringify({ type: 'params', ...params, seq: sentSeq.value }))
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
	window.removeEventListener('pointerup', stopDrag)
	window.removeEventListener('pointercancel', stopDrag)
	if (pseudoFullscreen) setPseudoFullscreen(false)
})

onMounted(() => {
	// Состояние кнопки следим по событию: из полноэкранного режима можно выйти и по Esc/жесту браузера
	document.addEventListener('fullscreenchange', syncFullscreen)
	document.addEventListener('webkitfullscreenchange', syncFullscreen)
	document.addEventListener('keydown', onKeydown)
	// Слайдер могут отпустить где угодно — слушаем всё окно
	window.addEventListener('pointerup', stopDrag)
	window.addEventListener('pointercancel', stopDrag)

	const width = canvasContainer.value.clientWidth
	const height = canvasContainer.value.clientHeight

	scene = new THREE.Scene()

	camera = new THREE.PerspectiveCamera(15, width / height, 10, 10000)
	camera.position.set(460, 460, 1125) // расстояние ≈ 1300 мм (как distance по умолчанию)
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

	// Тёплый основной свет: цветовой контраст с холодным «окном» делает освещение живее
	light = new THREE.DirectionalLight(LIGHT_COLOR, LIGHT_INTENSITY)
	// Свет и окружение закреплены в мире (как в комнате): при вращении вида освещение на модели меняется.
	// Начальное положение света считаем относительно стартовой камеры, затем переводим в мировые координаты.
	camera.updateMatrixWorld()
	const targetLocal = camera.worldToLocal(new THREE.Vector3(0, 0, 0))
	// Свет падает почти от зрителя: луч отклонён от направления взгляда на LIGHT_ANGLE
	// вправо и вверх (диагональ 45°).
	const dist = targetLocal.length()
	const side = dist * Math.sin(LIGHT_ANGLE) * Math.SQRT1_2
	light.position.copy(camera.localToWorld(new THREE.Vector3(side, side, targetLocal.z + dist * Math.cos(LIGHT_ANGLE))))
	light.target.position.set(0, 0, 0)
	scene.add(light, light.target)

	// Карта окружения (IBL) — нарисованная «комната».
	scene.environment = makeRoomEnvironment()
	scene.environmentIntensity = ENVIRONMENT_INTENSITY

	// Фон — та же комната (окно и лампа на тех же местах), но в тёмно-синих тонах и размытая.
	// Для освещения — нейтральная комната выше.
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
		<div v-if="limits" class="controls">
			<!-- Две группы: если все слайдеры не помещаются в строку, вторая группа переносится под X и Y -->
			<div class="sliders">
				<label v-for="key in TOP_SLIDERS" :key="key" class="slider">
					<small class="caption">{{ SLIDER_NAMES[key] }}</small>
					<div class="range" :style="{ '--pos': sliderPos(key) }" @pointerdown="dragged = key">
						<input v-model.number="params[key]" type="range" :min="limits[key].min" :max="limits[key].max" step="1" :disabled="switching" @input="sendParams" />
						<output>{{ params[key] }}</output>
					</div>
				</label>
			</div>
			<div class="nums">
				<label v-for="c in NUMBER_CONTROLS" :key="c.key" class="slider">
					<small class="caption">{{ SLIDER_NAMES[c.key] }}</small>
					<div class="range" :style="{ '--pos': sliderPos(c.key, c.geometric) }" @pointerdown="dragged = c.key">
						<input v-if="c.geometric" type="range" min="0" :max="GEOMETRIC_STEPS" :value="Math.round(sliderPos(c.key, true) * GEOMETRIC_STEPS)" :disabled="switching" @input="setGeometric(c, $event.target.value)" />
						<input v-else type="range" :min="limits[c.key].min" :max="sliderMax(c.key)" step="1" :value="params[c.key]" :disabled="switching" @input="setValue(c.key, $event.target.value)" />
						<output>{{ params[c.key] }}</output>
					</div>
				</label>
			</div>
		</div>

		<label v-if="limits" class="slider-z">
			<small class="caption">{{ SLIDER_NAMES.z }}</small>
			<div class="range" :style="{ '--pos': sliderPos('z') }" @pointerdown="dragged = 'z'">
				<input v-model.number="params.z" type="range" :min="limits.z.min" :max="limits.z.max" step="1" class="vertical" :disabled="switching" @input="changeZ" />
				<output>{{ params.z }}</output>
			</div>
		</label>

		<div ref="viewerArea" class="canvas-wrap" :class="{ 'is-fullscreen': isFullscreen }">
			<div ref="canvasContainer" class="canvas-box"></div>
			<div class="overlay" :class="{ active: isLoading || !connected }">
				<div class="spinner"></div>
				<div v-if="!connected">Подключение к генератору…</div>
			</div>
			<div v-if="dragged" class="drag-hint">{{ SLIDER_NAMES[dragged] }} <span>{{ params[dragged] }}</span>{{ dragged === 'perforation' ? '' : ' мм' }}</div>
			<button
				type="button"
				class="fullscreen-toggle"
				:title="fullscreenTitle"
				:aria-label="fullscreenTitle"
				@click="toggleFullscreen"
			>
				<!-- Развернуть: уголки наружу; свернуть: уголки внутрь -->
				<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<path :d="isFullscreen ? 'M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5' : 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5'" />
				</svg>
			</button>
		</div>

		<aside class="queue-panel">

			<div v-if="errorCount" class="queue-summary">
				<span class="stat error">Ошибок: {{ errorCount }}</span>
			</div>

			<ul class="queue-list">
				<li v-for="ev in reversedEvents" :key="ev.id" class="job" :class="ev.status">
					<div class="job-row">
						<span class="job-params" :title="ev.title">{{ ev.params }}</span>
						<span v-if="ev.size" class="job-size" title="Размер модели GLB">{{ ev.size }}</span>
						<span class="job-duration">{{ ev.time }}</span>
						<span v-if="ev.status !== 'done'" class="badge" :class="ev.status">{{ ev.status }}</span>
					</div>
					<div v-if="ev.error" class="job-error">{{ ev.error }}</div>
				</li>
				<li v-if="!events.length" class="empty">Моделей пока нет</li>
			</ul>

			<label v-if="gens.length" class="gen-select">
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
/* Широкий экран: слева ползунки, под ними Z и 3D-вид; справа журнал на всю высоту */
.layout {
	display: grid;
	grid-template-columns: auto minmax(0, 1fr) clamp(240px, 24vw, 320px);
	grid-template-rows: auto 1fr;
	gap: 12px;
	width: 100%;
	color: #e6edf6;
}
.queue-panel {
	grid-column: 3;
	grid-row: 1 / 3;
	display: flex;
	flex-direction: column;
	gap: 12px;
	min-height: 0;
	contain: size; /* высоту задают ползунки и 3D-вид */
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
	padding: 4px 10px;
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
	min-height: 20px; /* запись высотой 30px — как кружок ползунка: они в одном ряду */
	align-items: center;
	gap: 8px;
	white-space: nowrap;
}
.job-params {
	flex: 1;
	min-width: 0; /* в узкой панели обрезается многоточием */
	overflow: hidden;
	text-overflow: ellipsis;
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
.job-size,
.job-duration {
	font-size: 0.7rem;
	color: #94a9c4;
	font-variant-numeric: tabular-nums;
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
.controls {
	grid-column: 1 / 3;
	grid-row: 1;
	display: flex;
	flex-wrap: wrap;
	gap: 12px 16px;
	align-items: center;
}
label {
	display: flex;
	align-items: center;
	gap: 8px;
	font-size: 0.85rem;
	font-weight: 600;
	color: #94a9c4;
}
/* Обе группы занимают по половине строки: X и Y — одну, три остальных слайдера — другую.
   Ширина строки меньше «330px + 330px» — вторая группа переносится на новую строку */
.sliders,
.nums {
	display: flex;
	flex: 1 1 330px;
	gap: 16px;
	min-width: 0;
}
/* Подпись над слайдером: занимает всю строку, слайдер переносится под неё */
.caption {
	flex-basis: 100%;
	min-width: 0;
	overflow: hidden;
	white-space: nowrap;
	text-overflow: ellipsis;
	font-size: 14px;
	font-weight: 700;
	line-height: 16px;
}
label.slider {
	flex-wrap: wrap;
	row-gap: 2px;
	flex: 1 1 0; /* слайдеры группы делят строку поровну */
	min-width: 0;
}
/* Ползунок с числом внутри кружка. --pos (от 0 до 1) — положение кружка */
.range {
	--thumb: 30px;
	--track: 6px;
	position: relative;
	display: flex;
	flex: 1;
	min-width: 0;
}
input[type='range'] {
	flex: 1;
	min-width: 0;
	height: var(--thumb);
	margin: 0;
	padding: 0;
	border: none;
	background: transparent;
	outline: none;
	appearance: none;
	-webkit-appearance: none;
	cursor: pointer;
}
input[type='range']::-webkit-slider-runnable-track {
	height: var(--track);
	border-radius: 999px;
	background: linear-gradient(to right, #7dd3fc calc(var(--pos) * 100%), rgba(255, 255, 255, 0.15) 0);
}
input[type='range']::-webkit-slider-thumb {
	-webkit-appearance: none;
	width: var(--thumb);
	height: var(--thumb);
	margin-top: calc((var(--track) - var(--thumb)) / 2);
	border: none;
	border-radius: 50%;
	background: #7dd3fc;
}
input[type='range']::-moz-range-track {
	height: var(--track);
	border-radius: 999px;
	background: rgba(255, 255, 255, 0.15);
}
input[type='range']::-moz-range-progress {
	height: var(--track);
	border-radius: 999px;
	background: #7dd3fc;
}
input[type='range']::-moz-range-thumb {
	width: var(--thumb);
	height: var(--thumb);
	border: none;
	border-radius: 50%;
	background: #7dd3fc;
}
/* Число лежит поверх кружка; мышь и палец проходят сквозь него */
output {
	position: absolute;
	top: 50%;
	left: calc(var(--thumb) / 2 + (100% - var(--thumb)) * var(--pos));
	transform: translate(-50%, -50%);
	pointer-events: none;
	color: #0f1724;
	font-size: 0.75rem;
	font-weight: 700;
	font-variant-numeric: tabular-nums;
}
input:disabled {
	opacity: 0.35;
	cursor: default;
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
	grid-column: 2;
	grid-row: 2;
	position: relative;
	min-width: 0;
}
label.slider-z {
	grid-column: 1;
	grid-row: 2;
	flex-direction: column;
	width: auto;
}
input[type='range'].vertical {
	writing-mode: vertical-lr;
	direction: rtl; /* большее значение сверху */
	flex: none;
	width: var(--thumb);
	height: auto;
}
input[type='range'].vertical::-webkit-slider-runnable-track {
	width: var(--track);
	height: auto;
	background: linear-gradient(to top, #7dd3fc calc(var(--pos) * 100%), rgba(255, 255, 255, 0.15) 0);
}
input[type='range'].vertical::-webkit-slider-thumb {
	margin-top: 0;
	margin-left: calc((var(--track) - var(--thumb)) / 2);
}
input[type='range'].vertical::-moz-range-track,
input[type='range'].vertical::-moz-range-progress {
	width: var(--track);
}
input[type='range'].vertical::-moz-range-track {
	height: 100%; /* иначе остаётся высота горизонтальной дорожки, и вертикальную не видно */
}
/* У вертикального Z подпись тоже вертикальная, читается снизу вверх */
label.slider-z .caption {
	flex-basis: auto;
	writing-mode: vertical-rl;
	transform: rotate(180deg);
}
label.slider-z output {
	top: auto;
	bottom: calc(var(--thumb) / 2 + (100% - var(--thumb)) * var(--pos));
	left: 50%;
	transform: translate(-50%, 50%);
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
/* Кнопка поверх 3D-вида */
button.fullscreen-toggle {
	position: absolute;
	top: 10px;
	left: 10px;
	z-index: 2;
	width: 36px;
	height: 36px;
	padding: 0;
	display: inline-flex;
	align-items: center;
	justify-content: center;
	border-radius: 10px;
	background: rgba(15, 23, 36, 0.75);
	border: 1px solid rgba(125, 211, 252, 0.35);
	color: #7dd3fc;
	box-shadow: none;
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
/* Название и значение слайдера, который сейчас двигают. Тень — чтобы текст читался на любом фоне */
.drag-hint {
	position: absolute;
	right: 12px;
	bottom: 10px;
	z-index: 2; /* поверх затемнения со спиннером */
	pointer-events: none;
	font-size: 1rem;
	font-weight: 600;
	color: #e6edf6;
	text-shadow: 0 0 2px #000, 0 0 3px #000, 0 0 6px #000;
}
/* Место под число: самое длинное значение — 3 знака («256», «9.9»), надпись не сдвигается */
.drag-hint span {
	display: inline-block;
	min-width: 3ch;
	text-align: right; /* число прижато к «мм» */
	font-variant-numeric: tabular-nums;
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
/* Широкий экран: страница занимает всю высоту окна, 3D-вид — всё место под ползунками */
@media (min-width: 901px) {
	.layout {
		min-height: calc(100vh - 16px); /* 16px — отступы #app в welcome.blade.php */
		min-height: calc(100svh - 16px);
	}
	/* Первая запись журнала — в одном ряду со слайдерами, ниже их подписей (16px + 2px) */
	.queue-panel {
		padding-top: 18px;
	}
	.canvas-wrap {
		min-height: 280px;
	}
	.canvas-box {
		position: absolute; /* размер задаёт окно, а не нарисованный внутри canvas */
		inset: 0;
		height: auto;
	}
}
/* Узкий экран: журнал переходит вниз */
@media (max-width: 900px) {
	.layout {
		grid-template-columns: auto minmax(0, 1fr);
		grid-template-rows: auto;
	}
	.queue-panel {
		grid-column: 1 / 3;
		grid-row: 3;
		height: 280px;
	}
}
/* Телефоны: меньше отступы, чтобы больше места досталось элементам управления и модели */
@media (max-width: 600px) {
	.layout {
		gap: 8px;
	}
	.controls {
		gap: 10px;
	}
	.sliders,
	.nums {
		gap: 12px;
	}
}
/* Вертикальный экран: оставляем снизу полосу, за которую можно прокрутить страницу (жесты на модели её не прокручивают) */
@media (max-width: 900px) and (orientation: portrait) {
	.canvas-box {
		height: max(280px, 65vh);
		height: max(280px, 65svh); /* высота окна с учётом адресной строки браузера */
	}
}
</style>
