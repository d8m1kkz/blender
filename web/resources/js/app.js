import { createApp } from 'vue';
import CounterControl from './components/CounterControl.vue';
import ModelViewer from './components/ModelViewer.vue';

const app = createApp({});
app.component('counter-control', CounterControl);
app.component('model-viewer', ModelViewer);
app.mount('#app');
