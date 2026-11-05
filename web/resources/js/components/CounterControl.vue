<script setup>
import { ref } from 'vue'

const count = ref(0)
const step = ref(5)

const increment = () => {
    count.value += Number(step.value)
}

const decrement = () => {
    count.value -= Number(step.value)
}

const reset = () => {
    count.value = 0
}
</script>

<template>
    <div class="control-card">
        <h3>Интерактивный счетчик</h3>

        <div class="input-group">
            <label for="count">Текущее значение:</label>
            <input
                id="count"
                v-model.number="count"
                type="number"
            />
        </div>

        <div class="input-group">
            <label for="step">Шаг изменения:</label>
            <input
                id="step"
                v-model.number="step"
                type="number"
                min="1"
            />
        </div>

        <div class="buttons">
            <button @click="decrement">-{{ step }}</button>
            <button @click="reset">Сброс ({{ count }} {{ step }})</button>
            <button @click="increment">+{{ step }}</button>
        </div>
    </div>
</template>

<style scoped>
.control-card {
    max-width: 320px;
    padding: 1.5rem;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    font-family: sans-serif;
    background: #ffffff;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}
.display {
    font-size: 1.25rem;
    margin: 1rem 0;
}
.input-group {
    margin-bottom: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}
input {
    padding: 0.4rem;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
}
.buttons {
    display: flex;
    gap: 0.5rem;
}
button {
    flex: 1;
    padding: 0.5rem;
    border: none;
    border-radius: 4px;
    background: #2563eb;
    color: white;
    cursor: pointer;
}
button:hover {
    background: #1d4ed8;
}
</style>
