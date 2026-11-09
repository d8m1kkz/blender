<script setup>
import { ref, onMounted } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const canvasContainer = ref(null)
const x = ref(1)
const y = ref(2)

let scene, camera, renderer, currentModel
const loader = new GLTFLoader()

const loadModel = () => {
    const url = `http://localhost:8080?x=${x.value}&y=${y.value}`

    loader.load(url, (gltf) => {
        if (currentModel) {
            scene.remove(currentModel)
        }
        currentModel = gltf.scene
        scene.add(currentModel)
    })
}

onMounted(() => {
    const width = canvasContainer.value.clientWidth
    const height = canvasContainer.value.clientHeight

    scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0f1724)

    camera = new THREE.PerspectiveCamera(80, width / height, 0.1, 1000)
    camera.position.set(3, 3, 5)
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

    // Первая загрузка
    loadModel()
})
</script>

<template>
    <div class="viewer-wrapper">
        <header class="title">
            <span class="dot"></span>
            <h1>3D Model Viewer</h1>
        </header>

        <div class="controls">
            <label><span>X</span><input v-model.number="x" type="number" /></label>
            <label><span>Y</span><input v-model.number="y" type="number" /></label>
            <button @click="loadModel">Обновить модель</button>
        </div>

        <div ref="canvasContainer" class="canvas-box"></div>
    </div>
</template>

<style scoped>
.viewer-wrapper {
    display: flex;
    flex-direction: column;
    gap: 16px;
    width: 1000px;
    padding: 24px;
    border-radius: 20px;
    background: linear-gradient(160deg, #1e2d44 0%, #16212f 100%);
    border: 1px solid rgba(148, 197, 255, 0.15);
    box-shadow: 0 30px 60px -20px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(0, 0, 0, 0.3);
    color: #e6edf6;
}
.title {
    display: flex;
    align-items: center;
    gap: 12px;
}
.title h1 {
    margin: 0;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    background: linear-gradient(90deg, #7dd3fc, #a78bfa);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}
.dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #34d399;
    box-shadow: 0 0 12px #34d399;
}
.controls {
    display: flex;
    gap: 12px;
    align-items: center;
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
    margin-left: auto;
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
    height: 600px;
    overflow: hidden;
    border-radius: 14px;
    border: 1px solid rgba(125, 211, 252, 0.25);
    box-shadow: inset 0 0 40px rgba(0, 0, 0, 0.5);
}
.canvas-box :deep(canvas) {
    display: block;
}
</style>
