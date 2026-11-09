<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const canvasContainer = ref(null)
const x = ref(1)
const y = ref(2)

let scene, camera, renderer, currentModel, resizeObserver
const loader = new GLTFLoader()

// Журнал очереди запросов (для отладки)
const MAX_HISTORY = 50
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

const processQueue = async () => {
    if (processing) return
    processing = true
    try {
        let job
        while ((job = jobs.value.find((j) => j.status === 'pending'))) {
            job.status = 'loading'
            job.startedAt = Date.now()
            try {
                const gltf = await loader.loadAsync(`http://localhost:8080?x=${job.x}&y=${job.y}`)
                if (currentModel) {
                    scene.remove(currentModel)
                }
                currentModel = gltf.scene
                scene.add(currentModel)
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
    jobs.value.push({
        id: nextId++,
        x: x.value,
        y: y.value,
        status: 'pending',
        createdAt: Date.now(),
        startedAt: null,
        finishedAt: null,
        error: null,
    })
    processQueue()
}

onBeforeUnmount(() => resizeObserver?.disconnect())

onMounted(() => {
    const width = canvasContainer.value.clientWidth
    const height = canvasContainer.value.clientHeight

    scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0f1724)

    camera = new THREE.PerspectiveCamera(19, width / height, 0.1, 1000)
    camera.position.set(75, 75, 125)
    camera.lookAt(0, 0, 0)

    renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(width, height)
    canvasContainer.value.appendChild(renderer.domElement)

    // Базовое освещение сцены
    const light = new THREE.DirectionalLight(0xffffff, 2)
    light.position.set(5, 10, 7)
    scene.add(light)
    scene.add(new THREE.AmbientLight(0xffffff, 1))

    // Цикл рендеринга с медленным вращением для наглядности
    const animate = () => {
        requestAnimationFrame(animate)
        if (currentModel) {
            currentModel.rotation.y += 0.01
        }
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
                    <input v-model.number="x" type="range" min="0" max="100" step="1" @change="loadModel" />
                    <output>{{ x }}</output>
                </label>
                <label class="slider">
                    <span>Y</span>
                    <input v-model.number="y" type="range" min="0" max="100" step="1" @change="loadModel" />
                    <output>{{ y }}</output>
                </label>
            </div>

            <div class="canvas-wrap" style="position: relative">
                <div ref="canvasContainer" class="canvas-box"></div>
                <div class="overlay" :class="{ active: isLoading }" style="position: absolute; inset: 0">
                    <div class="spinner"></div>
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
                        <span class="job-params">x={{ job.x }}, y={{ job.y }}</span>
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
