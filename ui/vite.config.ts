import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// In development (`npm run dev`), forward API, WebSocket and camera requests to the Python server.
const api = process.env.TINKERPNP_API ?? 'http://127.0.0.1:7300';

export default defineConfig({
	server: {
		proxy: {
			'/api': api,
			'/cameras': api,
			'/files': api,
			'/websocket': { target: api.replace(/^http/, 'ws'), ws: true }
		}
	},
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// adapter-auto only supports some environments, see https://svelte.dev/docs/kit/adapter-auto for a list.
			// If your environment is not supported, or you settled on a specific environment, switch out the adapter.
			// See https://svelte.dev/docs/kit/adapters for more information about adapters.
			adapter: adapter({ fallback: 'index.html' }) // static SPA, served by the Python server
		})
	]
});
