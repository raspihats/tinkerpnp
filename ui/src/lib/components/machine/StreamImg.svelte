<script lang="ts">
	// A camera picture that doesn't hog the browser's connections. Browsers open at most 6 connections to one server,
	// and every live MJPEG stream keeps one open: a few streams across a few tabs and new ones never start (seen on
	// the bench). So:
	//   live: MJPEG stream, but only while the tab is visible (a hidden tab gives its connection back)
	//   poll: one snapshot at a time at `fps` (connections are short), for thumbnails and secondary views
	import { onMount } from 'svelte';
	import { streamSettings } from '#lib/stream.svelte.js';

	// a 1x1 transparent GIF: switching an <img> to it is what makes the browser abort a running MJPEG download
	// (removing the element isn't enough in Chrome)
	const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

	let {
		camera,
		mode = 'live',
		fps = 1,
		overlay = '',
		alt,
		class: className = '',
		img = $bindable(null),
		...rest
	}: {
		camera: string;
		mode?: 'live' | 'poll';
		fps?: number;
		overlay?: string;
		alt: string;
		class?: string;
		img?: HTMLImageElement | null;
		[k: string]: unknown;
	} = $props();

	let visible = $state(true);
	let polled = $state<string | null>(null);

	// low bandwidth: the live stream also gets a lower fps; polled snapshots keep their own `fps`
	function query(live: boolean): string {
		const p = new URLSearchParams(streamSettings.params());
		if (!live) p.delete('fps');
		if (overlay) p.set('overlay', overlay);
		const q = p.toString();
		return q ? `?${q}` : '';
	}
	const liveSrc = $derived(visible ? `/cameras/${camera}/stream.mjpg${query(true)}` : BLANK);

	onMount(() => {
		const update = () => (visible = document.visibilityState === 'visible');
		update();
		document.addEventListener('visibilitychange', update);
		return () => {
			document.removeEventListener('visibilitychange', update);
			if (img && mode === 'live') img.src = BLANK; // closing the view: end the stream now
		};
	});

	// poll mode: fetch a snapshot, show it, wait, repeat (only while visible)
	$effect(() => {
		if (mode !== 'poll' || !visible) return;
		const url = `/cameras/${camera}/snapshot.jpg${query(false)}`;
		let stopped = false;
		let last: string | null = null;
		(async () => {
			while (!stopped) {
				const t0 = performance.now();
				try {
					const r = await fetch(url, { cache: 'no-store' });
					if (r.ok && !stopped) {
						const next = URL.createObjectURL(await r.blob());
						polled = next;
						if (last) URL.revokeObjectURL(last);
						last = next;
					}
				} catch {
					/* server restarting: try again */
				}
				const wait = Math.max(100, 1000 / fps - (performance.now() - t0));
				await new Promise((res) => setTimeout(res, wait));
			}
		})();
		return () => {
			stopped = true;
			if (last) URL.revokeObjectURL(last);
		};
	});
</script>

{#if mode === 'live'}
	<img bind:this={img} src={liveSrc} {alt} class={className} {...rest} />
{:else if polled}
	<img bind:this={img} src={polled} {alt} class={className} {...rest} />
{:else}
	<div class={className}></div>
{/if}
