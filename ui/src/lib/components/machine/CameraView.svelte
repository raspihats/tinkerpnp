<script lang="ts">
	// Live view of one camera. On a head-mounted, down-looking camera with units_per_pixel, a click moves the camera
	// so that the clicked point comes under the crosshair (like OpenPnP's camera view).
	import { tinker, fmt } from '#lib/api/client.svelte.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { MousePointerClick } from '@lucide/svelte';

	let { id, headMounted = false }: { id: string; headMounted?: boolean } = $props();

	const OVERLAYS = ['crosshair', 'scale', 'grid', 'info'] as const;
	let overlays = $state<string[]>(['crosshair', 'scale']);

	const s = $derived(tinker.status[`camera/${id}`] ?? {});
	const upp = $derived(s.units_per_pixel as { x: number; y: number } | null | undefined);
	const loc = $derived((s.location ?? {}) as { x?: number; y?: number; z?: number });
	const movable = $derived(headMounted && s.looking === 'down' && !!upp && tinker.status['machine']?.homed === true);
	const src = $derived(`/cameras/${id}/stream.mjpg${overlays.length ? `?overlay=${overlays.join(',')}` : ''}`);

	let cursor = $state<{ x: number; y: number } | null>(null);

	function toMachine(e: MouseEvent): { x: number; y: number } | null {
		const img = e.currentTarget as HTMLImageElement;
		if (!upp || !img.naturalWidth || loc.x === undefined || loc.y === undefined) return null;
		const r = img.getBoundingClientRect();
		const px = ((e.clientX - r.left) / r.width) * img.naturalWidth;
		const py = ((e.clientY - r.top) / r.height) * img.naturalHeight;
		return {
			x: loc.x + (px - img.naturalWidth / 2) * upp.x,
			y: loc.y - (py - img.naturalHeight / 2) * upp.y
		};
	}

	function onMove(e: MouseEvent) {
		cursor = toMachine(e);
	}

	function onClick(e: MouseEvent) {
		if (!movable) return;
		const target = toMachine(e);
		if (target) tinker.act('motion.move_to', { object: `camera/${id}`, location: target });
	}

	function toggle(o: string) {
		overlays = overlays.includes(o) ? overlays.filter((x) => x !== o) : [...overlays, o];
	}
</script>

<div class="flex flex-col gap-1.5">
	<div class="overflow-hidden rounded-md bg-black">
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
		<img
			{src}
			alt="camera {id}"
			class="block h-auto w-full {movable ? 'cursor-crosshair' : ''}"
			onclick={onClick}
			onmousemove={onMove}
			onmouseleave={() => (cursor = null)}
		/>
	</div>
	<div class="flex flex-wrap items-center gap-1 text-xs">
		{#each OVERLAYS as o (o)}
			<button onclick={() => toggle(o)}><Badge variant={overlays.includes(o) ? 'default' : 'outline'}>{o}</Badge></button>
		{/each}
		<span class="ml-auto font-mono text-muted-foreground tabular-nums">
			{#if cursor}
				→ X {fmt(cursor.x, 2)} Y {fmt(cursor.y, 2)}
			{:else}
				X {fmt(loc.x, 2)} Y {fmt(loc.y, 2)} · {fmt(s.fps, 0)} fps
			{/if}
		</span>
		{#if movable}
			<span class="flex items-center gap-1 text-muted-foreground" title="Click in the image to move the camera there">
				<MousePointerClick class="size-3.5" /> click to move
			</span>
		{/if}
	</div>
	{#if s.last_error}<p class="text-xs text-destructive">{s.last_error}</p>{/if}
</div>
