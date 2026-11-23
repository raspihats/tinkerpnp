<script lang="ts">
	// Live view of one camera. On a head-mounted, down-looking camera with units_per_pixel, a click moves the camera
	// so that the clicked point comes under the crosshair (like OpenPnP's camera view).
	import { tinker, fmt, idOf } from '#lib/api/client.svelte.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { MousePointerClick } from '@lucide/svelte';

	let { id, headMounted = false, tool = null }: { id: string; headMounted?: boolean; tool?: string | null } = $props();

	const OVERLAYS = ['crosshair', 'scale', 'grid', 'info'] as const;
	let overlays = $state<string[]>(['crosshair', 'scale']);

	const s = $derived(tinker.status[`camera/${id}`] ?? {});
	const upp = $derived(s.units_per_pixel as { x: number; y: number } | null | undefined);
	const loc = $derived((s.location ?? {}) as { x?: number; y?: number; z?: number });
	const movable = $derived(headMounted && s.looking === 'down' && !!upp && tinker.status['machine']?.homed === true);
	const src = $derived(`/cameras/${id}/stream.mjpg${overlays.length ? `?overlay=${overlays.join(',')}` : ''}`);

	let cursor = $state<{ x: number; y: number } | null>(null);

	// Up-looking machine cameras: move the selected nozzle over the camera, or capture the camera's location from
	// where the nozzle is (centred and in focus), like OpenPnP's "capture tool location".
	const nozzle = $derived(tool?.startsWith('nozzle/') ? tool : null);
	const machineCamUp = $derived(!headMounted && s.looking === 'up');
	const homed = $derived(tinker.status['machine']?.homed === true);
	let captured = $state<{ x: number; y: number; z: number } | null>(null);

	function moveHere() {
		if (!nozzle || loc.x === undefined || loc.y === undefined) return;
		const target: Record<string, number> = { x: loc.x, y: loc.y };
		if (loc.z !== undefined) target.z = loc.z;
		tinker.act('motion.move_to', { object: nozzle, location: target });
	}

	async function capture() {
		if (!nozzle) return;
		const r = await tinker.act<{ value: { x: number; y: number; z: number } }>('config.capture', {
			field: `cameras.${id}.location`,
			from: 'location',
			args: { object: nozzle }
		});
		if (r) captured = r.value;
	}

	type UppResult = {
		value: { x: number; y: number };
		details: { field_of_view_mm: number[]; rotation_deg: number; mirrored_x: boolean; mirrored_y: boolean; match: number[] };
	};
	let upp_result = $state<UppResult | null>(null);
	const measuring = $derived(tinker.isBusy('config.capture'));

	async function measureUpp() {
		if (!nozzle) return;
		upp_result = await tinker.act<UppResult>('config.capture', {
			field: `cameras.${id}.units_per_pixel`,
			from: 'units_per_pixel',
			args: { camera: id, object: nozzle, distance: 1.0 }
		});
	}

	async function saveUpp() {
		if (!upp_result) return;
		const v = upp_result.value;
		const r = await tinker.act('config.update', {
			changes: [
				{ op: 'set', path: `cameras.${id}.units_per_pixel.x`, value: v.x },
				{ op: 'set', path: `cameras.${id}.units_per_pixel.y`, value: v.y }
			]
		});
		if (r) upp_result = null;
	}

	async function saveCapture() {
		if (!captured) return;
		const changes = (['x', 'y', 'z'] as const).map((k) => ({ op: 'set', path: `cameras.${id}.location.${k}`, value: captured![k] }));
		const r = await tinker.act('config.update', { changes });
		if (r) captured = null;
	}

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
	{#if machineCamUp && nozzle}
		<div class="flex flex-wrap items-center gap-1 text-xs">
			<Button variant="outline" size="sm" disabled={!homed} onclick={moveHere} title="Move {idOf(nozzle)} to this camera's location (safe Z first)">
				Move {idOf(nozzle)} here
			</Button>
			<Button variant="outline" size="sm" disabled={!homed} onclick={capture} title="Use where {idOf(nozzle)} is now as this camera's location">
				Set location from {idOf(nozzle)}
			</Button>
			<Button variant="outline" size="sm" disabled={!homed || measuring} onclick={measureUpp}
				title="With {idOf(nozzle)} centred and in focus: move it 1 mm in X and in Y at this height and measure how far the image moves">
				{measuring ? 'Measuring…' : `Measure mm/px with ${idOf(nozzle)}`}
			</Button>
			{#if captured}
				<span class="rounded bg-muted px-2 py-1 font-mono">
					X {fmt(loc.x, 3)} → {fmt(captured.x, 3)} · Y {fmt(loc.y, 3)} → {fmt(captured.y, 3)} · Z {fmt(loc.z, 3)} → {fmt(captured.z, 3)}
				</span>
				<Button size="sm" onclick={saveCapture}>Save</Button>
				<Button variant="ghost" size="sm" onclick={() => (captured = null)}>Cancel</Button>
			{/if}
			{#if upp_result}
				{@const d = upp_result.details}
				<span class="rounded bg-muted px-2 py-1 font-mono">
					{fmt(upp?.x, 5)} × {fmt(upp?.y, 5)} → {upp_result.value.x.toFixed(5)} × {upp_result.value.y.toFixed(5)} mm/px
					· view {d.field_of_view_mm[0]} × {d.field_of_view_mm[1]} mm · rotated {d.rotation_deg}°
					{#if d.mirrored_x || d.mirrored_y}· <span class="text-amber-700 dark:text-amber-400">mirrored {d.mirrored_x ? 'X' : ''}{d.mirrored_y ? 'Y' : ''} (set transform.flip_{d.mirrored_x ? 'x' : 'y'})</span>{/if}
				</span>
				<Button size="sm" onclick={saveUpp}>Save</Button>
				<Button variant="ghost" size="sm" onclick={() => (upp_result = null)}>Cancel</Button>
			{/if}
		</div>
	{/if}
	{#if s.last_error}<p class="text-xs text-destructive">{s.last_error}</p>{/if}
</div>
