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
	max-width: 340px;
	padding: 1.75rem;
	border: 1px solid rgba(148, 197, 255, 0.15);
	border-radius: 18px;
	font-family: inherit;
	background: linear-gradient(160deg, #1e2d44, #16212f);
	color: #e6edf6;
	box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.6);
}
h3 {
	margin: 0 0 1.25rem;
	font-size: 1.15rem;
	background: linear-gradient(90deg, #7dd3fc, #a78bfa);
	-webkit-background-clip: text;
	background-clip: text;
	color: transparent;
}
.input-group {
	margin-bottom: 1rem;
	display: flex;
	flex-direction: column;
	gap: 0.35rem;
	font-size: 0.85rem;
	color: #94a9c4;
}
input {
	padding: 0.5rem 0.6rem;
	border: 1px solid rgba(255, 255, 255, 0.12);
	border-radius: 8px;
	background: #0f1724;
	color: #e6edf6;
	outline: none;
}
input:focus {
	border-color: #7dd3fc;
	box-shadow: 0 0 0 3px rgba(125, 211, 252, 0.2);
}
.buttons {
	display: flex;
	gap: 0.5rem;
}
button {
	flex: 1;
	padding: 0.55rem;
	border: none;
	border-radius: 10px;
	background: linear-gradient(90deg, #3b82f6, #8b5cf6);
	color: white;
	font-weight: 600;
	cursor: pointer;
	transition: transform 0.15s, filter 0.15s;
}
button:hover {
	filter: brightness(1.12);
	transform: translateY(-1px);
}
</style>
