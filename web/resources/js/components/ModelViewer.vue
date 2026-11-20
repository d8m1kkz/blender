<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

const canvasContainer = ref(null)
const x = ref(100)
const y = ref(100)
const z = ref(50)

// turntable — «поворотный столик»: в нём стол принтера и модель, автовращение крутит его целиком
let scene, camera, renderer, controls, turntable, bed, currentModel, resizeObserver
const loader = new GLTFLoader()

// Журнал очереди запросов (для отладки)
const MAX_HISTORY = 20
const jobs = ref([])
let nextId = 1
let processing = false

const pendingCount = computed(() => jobs.value.filter((j) => j.status === 'pending').length)
const doneCount = computed(() => jobs.value.filter((j) => j.status === 'done').length)
const errorCount = computed(() => jobs.value.filter((j) => j.status === 'error').length)
const isLoading = computed(() => jobs.value.some((j) => j.status === 'pending' || j.status === 'loading'))
const reversedJobs =computed(() => [...jobs.value].reverse())

const trimHistory = () => {
	let finished = jobs.value.filter((j) => j.status === 'done' || j.status === 'error').length
	while (finished > MAX_HISTORY) {
		const idx = jobs.value.findIndex((j) => j.status === 'done' || j.status === 'error')
		jobs.value.splice(idx, 1)
		finished--
	}
}

const duration = (job) => (job.finishedAt ? `${job.finishedAt - job.startedAt} мс` : '')

// Пока gen прогревает Blender, nginx отвечает 502/503/504 — это временно, повторяем запрос
const WARMUP_RETRY_DELAY = 500
const WARMUP_MAX_WAIT = 90000
const warmingUp = ref(false)

const isNotReady = (e) => /responded with (502|503|504)\b/.test(e?.message || '')
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const loadWithWarmup = async (url) => {
	const startedAt = Date.now()
	while (true) {
		try {
			const gltf = await loader.loadAsync(url)
			warmingUp.value = false
			return gltf
		} catch (e) {
			if (!isNotReady(e) || Date.now() - startedAt > WARMUP_MAX_WAIT) {
				warmingUp.value = false
				throw e
			}
			warmingUp.value = true
			await sleep(WARMUP_RETRY_DELAY)
		}
	}
}

// Материал PETG: зелёный глянцевый пластик с лёгким лаковым слоем (блики от печати/самого пластика).
// Один общий материал на все меши модели; цвет и шероховатость подбираются здесь.
const petgMaterial = new THREE.MeshPhysicalMaterial({
	color: 0x24663a,
	metalness: 0,
	roughness: 0.38,
	clearcoat: 0.3,
	clearcoatRoughness: 0.3,
})

// Слои 3D-печати: процедурный рельеф в шейдере, без текстуры и UV.
// Высота «валика» слоя зависит только от мировой Y, поэтому полоски всегда горизонтальны.
// Геометрия не меняется — отклоняется только нормаль (как у bump-карты), рельеф «рисует» свет.
const LAYER_HEIGHT = 0.2 // толщина слоя, мм (единицы сцены)
const LAYER_DEPTH = 0.003 // глубина бороздки между слоями, мм
// Затухание против ряби: полоски видны, только когда слой занимает достаточно пикселей.
const LAYER_FADE_FROM_PX = 4 // меньше стольких пикселей на слой — полосок нет совсем
const LAYER_FADE_TO_PX = 20 // от стольких пикселей на слой — полоски видны полностью

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

const processQueue = async () => {
	if (processing) return
	processing = true
	try {
		let job
		while ((job = jobs.value.find((j) => j.status === 'pending'))) {
			job.status = 'loading'
			job.startedAt = Date.now()
			try {
				const gltf = await loadWithWarmup(`/generate?x=${job.x}&y=${job.y}&z=${job.z}`)
				if (currentModel) {
					turntable.remove(currentModel)
					disposeModel(currentModel)
				}
				currentModel = gltf.scene
				prepareModel(currentModel)
				// Слои печати отсчитываются от дна модели (той же высоты, где стоит стол)
				layerUniforms.uLayerBase.value = placeBed(bed, currentModel)
				turntable.add(currentModel)
				job.status = 'done'
			} catch (e) {
				job.status = 'error'
				job.error = e?.message || String(e)
			}
			job.finishedAt = Date.now()
			trimHistory()
		}
	} finally {
		processing = false
	}
}

// Ставит запрос на обновление модели в очередь
const loadModel = () => {
	// Оставляем только выполняющееся и самое последнее задание: прежнее ожидающее выбрасываем
	jobs.value = jobs.value.filter((j) => j.status !== 'pending')
	jobs.value.push({
		id: nextId++,
		x: x.value,
		y: y.value,
		z: z.value,
		status: 'pending',
		createdAt: Date.now(),
		startedAt: null,
		finishedAt: null,
		error: null,
	})
	processQueue()
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

// Нейтральные стены — для освещения (IBL)
const ROOM_WALLS_LIGHTING = [
	[0, '#5c616b'], // потолок
	[0.35, '#474c55'],
	[0.5, '#3a3e46'], // горизонт
	[0.7, '#2a2c32'],
	[1, '#1d1e22'], // пол
]
// Стены в цвет карточек интерфейса (#1e2d44 → #16212f) — для видимого фона
const ROOM_WALLS_BACKGROUND = [
	[0, '#34496a'], // потолок
	[0.35, '#263753'],
	[0.5, '#1e2d44'], // горизонт
	[0.7, '#16212f'],
	[1, '#0e1520'], // пол
]

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
const BED_SIZE = 256 // стол Bambu Lab A1: 256×256 (единицы сцены = мм)
const BED_CELL = 25 // шаг сетки

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
	camera.position.set(250, 250, 500)
	camera.lookAt(0, 0, 0)

	renderer = new THREE.WebGLRenderer({ antialias: true })
	// Чёткость на экранах с высокой плотностью, но не больше 2x (экономим GPU на планшетах)
	renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
	renderer.setSize(width, height)
	// Тон-маппинг: без пересветов, плавные переходы как у камеры
	renderer.toneMapping = THREE.ACESFilmicToneMapping
	renderer.toneMappingExposure = 1.0
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
	const light = new THREE.DirectionalLight(0xfff0dd, 1.4)
	// Свет и окружение закреплены в мире (как в комнате): при вращении вида освещение на модели меняется.
	// Начальное положение света считаем относительно стартовой камеры, затем переводим в мировые координаты.
	camera.updateMatrixWorld()
	const targetLocal = camera.worldToLocal(new THREE.Vector3(0, 0, 0))
	// Свет падает почти от зрителя: луч отклонён от направления взгляда на LIGHT_ANGLE
	// вправо и вверх (диагональ 45°). Меняйте LIGHT_ANGLE, чтобы сделать свет косее или прямее.
	const LIGHT_ANGLE = THREE.MathUtils.degToRad(10)
	const dist = targetLocal.length()
	const side = dist * Math.sin(LIGHT_ANGLE) * Math.SQRT1_2
	light.position.copy(camera.localToWorld(new THREE.Vector3(side, side, targetLocal.z + dist * Math.cos(LIGHT_ANGLE))))
	light.target.position.set(0, 0, 0)
	scene.add(light, light.target)

	// Карта окружения (IBL) — нарисованная «комната». Строится один раз, на кадры почти не влияет.
	scene.environment = makeRoomEnvironment()
	scene.environmentIntensity = 2

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
		turntable.rotation.y += 0.005
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

	// Первая загрузка
	loadModel()
})
</script>

<template>
	<div class="layout">
		<div class="viewer-wrapper">

			<div class="controls">
				<label class="slider">
					<span>X</span>
					<input v-model.number="x" type="range" min="20" max="256" step="1" @input="loadModel" />
					<output>{{ x }}</output>
				</label>
				<label class="slider">
					<span>Y</span>
					<input v-model.number="y" type="range" min="20" max="256" step="1" @input="loadModel" />
					<output>{{ y }}</output>
				</label>
			</div>

			<div class="viewer-row">
				<label class="slider-z">
					<span>Z</span>
					<input v-model.number="z" type="range" min="20" max="150" step="1" class="vertical" @input="loadModel" />
					<output>{{ z }}</output>
				</label>

				<div ref="viewerArea" class="canvas-wrap" :class="{ 'is-fullscreen': isFullscreen }">
					<div ref="canvasContainer" class="canvas-box"></div>
					<div class="overlay" :class="{ active: isLoading }" style="position: absolute; inset: 0">
						<div class="spinner"></div>
						<div v-if="warmingUp">Прогрев Blender…</div>
					</div>
					<button
						type="button"
						class="view-btn fullscreen-toggle"
						:title="isFullscreen ? 'Выйти из полноэкранного режима' : 'Во весь экран'"
						:aria-label="isFullscreen ? 'Выйти из полноэкранного режима' : 'Во весь экран'"
						@click="toggleFullscreen"
					>
						<!-- Развернуть: уголки наружу; свернуть: уголки внутрь -->
						<svg v-if="!isFullscreen" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
							<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
						</svg>
						<svg v-else viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
							<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
						</svg>
					</button>
					<button type="button" class="view-btn reset-view" title="Вернуть исходный вид" aria-label="Вернуть исходный вид" @click="resetView">⟲</button>
				</div>
			</div>
		</div>

		<aside class="queue-panel">

			<div class="queue-summary">
				<span class="stat pending">В очереди: {{ pendingCount }}</span>
				<span class="stat done">Готово: {{ doneCount }}</span>
				<span v-if="errorCount" class="stat error">Ошибок: {{ errorCount }}</span>
			</div>

			<ul class="queue-list">
				<li v-for="job in reversedJobs" :key="job.id" class="job" :class="job.status">
					<div class="job-row">
						<span class="job-id">#{{ job.id }}</span>
						<span class="job-params">x={{ job.x }}, y={{ job.y }}, z={{ job.z }}</span>
						<span v-if="job.finishedAt" class="job-duration">{{ duration(job) }}</span>
						<span class="badge" :class="job.status">{{ job.status }}</span>
					</div>
					<div v-if="job.error" class="job-error">{{ job.error }}</div>
				</li>
				<li v-if="!jobs.length" class="empty">Запросов пока нет</li>
			</ul>
		</aside>
	</div>
</template>

<style scoped>
.layout {
	display: flex;
	align-items: stretch;
	gap: 24px;
	width: 100%;
}
.queue-panel {
	display: flex;
	flex-direction: column;
	gap: 12px;
	flex: 0 0 clamp(240px, 24vw, 320px);
	min-height: 0;
	contain: size; /* высоту задаёт вьювер, а не длина списка */
	padding: 24px 18px;
	box-sizing: border-box;
	border-radius: 20px;
	background: linear-gradient(160deg, #1e2d44 0%, #16212f 100%);
	border: 1px solid rgba(148, 197, 255, 0.15);
	box-shadow: 0 30px 60px -20px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(0, 0, 0, 0.3);
	color: #e6edf6;
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
.stat.pending { color: #94a9c4; }
.stat.done { color: #34d399; }
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
.job.pending { border-left-color: #94a9c4; }
.job.loading { border-left-color: #7dd3fc; }
.job.done { border-left-color: #34d399; }
.job.error { border-left-color: #f87171; }
.job-row {
	display: flex;
	align-items: center;
	gap: 8px;
}
.job-id {
	color: #94a9c4;
	font-variant-numeric: tabular-nums;
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
.badge.pending { color: #94a9c4; }
.badge.loading { color: #7dd3fc; animation: pulse 1s ease-in-out infinite; }
.badge.done { color: #34d399; }
.badge.error { color: #f87171; }
@keyframes pulse {
	50% { opacity: 0.4; }
}
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
	box-sizing: border-box;
	border-radius: 20px;
	background: linear-gradient(160deg, #1e2d44 0%, #16212f 100%);
	border: 1px solid rgba(148, 197, 255, 0.15);
	box-shadow: 0 30px 60px -20px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(0, 0, 0, 0.3);
	color: #e6edf6;
}
.controls {
	display: flex;
	flex-direction: column;
	gap: 12px;
	align-items: stretch;
	padding: 12px 14px;
	border-radius: 14px;
	background: rgba(255, 255, 255, 0.04);
	border: 1px solid rgba(255, 255, 255, 0.08);
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
	width: 100%;
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
	align-self: flex-end;
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
	border-radius: 14px;
	background: rgba(255, 255, 255, 0.04);
	border: 1px solid rgba(255, 255, 255, 0.08);
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
@media (max-width: 480px) {
	button {
		align-self: stretch;
	}
}
</style>
