import { defineConfig } from 'vite';
import laravel from 'laravel-vite-plugin';
import { bunny } from 'laravel-vite-plugin/fonts';
import tailwindcss from '@tailwindcss/vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
	plugins: [
		laravel({
			publicDirectory: 'public_html',
			input: ['resources/css/app.css', 'resources/js/app.js'],
			refresh: true,
			fonts: [
				bunny('Instrument Sans', {
					weights: [400, 500, 600],
				}),
			],
		}),
		vue(),
		tailwindcss(),
	],
	resolve: {
		alias: {
			vue: 'vue/dist/vue.esm-bundler.js',
		},
	},
	server: {
		host: '0.0.0.0',
		port: 5173,
		hmr: {
			host: 'localhost',
		},
		watch: {
			ignored: ['**/storage/framework/views/**'],
			usePolling: true,
			interval: 300,
		},
	},
});
