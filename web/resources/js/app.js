import { createApp } from 'vue';
import ModelViewer from './components/ModelViewer.vue';

const app = createApp({});
app.component('model-viewer', ModelViewer);
app.mount('#app');
