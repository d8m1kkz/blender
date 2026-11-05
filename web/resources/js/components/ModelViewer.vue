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
    scene.background = new THREE.Color(0xf1f5f9)

    camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
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
        <div class="controls">
            <label>X: <input v-model.number="x" type="number" /></label>
            <label>Y: <input v-model.number="y" type="number" /></label>
            <button @click="loadModel">Обновить модель</button>
        </div>

        <div ref="canvasContainer" class="canvas-box"></div>
    </div>
</template>

<style scoped>
.viewer-wrapper {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 500px;
}
.controls {
    display: flex;
    gap: 10px;
    align-items: center;
}
input {
    width: 60px;
    padding: 4px;
}
button {
    padding: 5px 10px;
    cursor: pointer;
}
.canvas-box {
    width: 500px;
    height: 400px;
    border: 1px solid #cbd5e1;
}
</style>
