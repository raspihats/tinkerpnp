<script lang="ts">
	// Live view of one camera. On a head-mounted, down-looking camera with units_per_pixel, a click moves the camera
	// so that the clicked point comes under the crosshair (like OpenPnP's camera view).
	import { tinker, fmt, idOf } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { MousePointerClick, Crosshair, Ruler, LocateFixed, Check, X, LoaderCircle, TriangleAlert } from '@lucide/svelte';

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

</script>

<div class="flex flex-col gap-3">
	<div class="relative overflow-hidden rounded-lg bg-black ring-1 ring-black/10">
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
		<img
			{src}
			alt="camera {id}"
			class="block aspect-[4/3] h-auto w-full object-contain {movable ? 'cursor-crosshair' : ''}"
			onclick={onClick}
			onmousemove={onMove}
			onmouseleave={() => (cursor = null)}
		/>
		<!-- heads-up readouts on the image -->
		<div class="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-2 text-[11px]">
			<span class="num rounded bg-black/60 px-1.5 py-0.5 text-white/90">
				{s.looking === 'up' ? '↑' : '↓'} X {fmt(loc.x, 2)} Y {fmt(loc.y, 2)}{loc.z !== undefined ? ` Z ${fmt(loc.z, 2)}` : ''}
			</span>
			<span class="num rounded bg-black/60 px-1.5 py-0.5 text-white/90">
				{s.width ?? '–'}×{s.height ?? '–'} · {fmt(s.fps, 0)} fps
			</span>
		</div>
		{#if cursor}
			<span class="num pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-primary px-2 py-0.5 text-[11px] font-semibold text-primary-foreground">
				{movable ? 'click → ' : ''}X {fmt(cursor.x, 2)} Y {fmt(cursor.y, 2)}
			</span>
		{/if}
	</div>

	<div class="flex flex-wrap items-center gap-2">
		<ToggleGroup.Root type="multiple" variant="outline" size="sm" bind:value={overlays} aria-label="overlays">
			{#each OVERLAYS as o (o)}
				<ToggleGroup.Item value={o} class="capitalize {segmentItem}">{o}</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
		{#if movable}
			<span class="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
				<MousePointerClick class="size-3.5" /> Click in the image to move there
			</span>
		{/if}
	</div>

	{#if machineCamUp && nozzle}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="eyebrow mr-1">Calibrate with {idOf(nozzle)}</span>
			<Button variant="outline" size="sm" disabled={!homed} onclick={moveHere} title="Move {idOf(nozzle)} to this camera's location (safe Z first)">
				<LocateFixed /> Move here
			</Button>
			<Button variant="outline" size="sm" disabled={!homed} onclick={capture} title="Use where {idOf(nozzle)} is now (centred, in focus) as this camera's location">
				<Crosshair /> Set location
			</Button>
			<Button variant="outline" size="sm" disabled={!homed || measuring} onclick={measureUpp}
				title="With {idOf(nozzle)} centred and in focus: move it 0.5 and 1 mm in X and Y at this height and measure how far the image moves">
				{#if measuring}<LoaderCircle class="animate-spin" /> Measuring…{:else}<Ruler /> Measure mm/px{/if}
			</Button>
		</div>
		{#if captured}
			<div class="flex flex-wrap items-center gap-3 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
				<span class="font-medium">New location</span>
				{#each ['x', 'y', 'z'] as const as k (k)}
					<span class="num"><span class="text-muted-foreground uppercase">{k}</span> {fmt(loc[k], 3)} → <b>{fmt(captured[k], 3)}</b></span>
				{/each}
				<span class="ml-auto flex gap-1">
					<Button size="sm" onclick={saveCapture}><Check /> Save</Button>
					<Button variant="ghost" size="sm" onclick={() => (captured = null)}><X /> Cancel</Button>
				</span>
			</div>
		{/if}
		{#if upp_result}
			{@const d = upp_result.details}
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
				<span class="font-medium">Measured</span>
				<span class="num">{fmt(upp?.x, 5)} × {fmt(upp?.y, 5)} → <b>{upp_result.value.x.toFixed(5)} × {upp_result.value.y.toFixed(5)}</b> mm/px</span>
				<span class="text-muted-foreground">view {d.field_of_view_mm[0]} × {d.field_of_view_mm[1]} mm · rotated {d.rotation_deg}°</span>
				{#if d.mirrored_x || d.mirrored_y}<span class="text-warn">mirrored in {d.mirrored_x ? 'X' : ''}{d.mirrored_y ? 'Y' : ''}</span>{/if}
				<span class="ml-auto flex gap-1">
					<Button size="sm" onclick={saveUpp}><Check /> Save</Button>
					<Button variant="ghost" size="sm" onclick={() => (upp_result = null)}><X /> Cancel</Button>
				</span>
			</div>
		{/if}
	{/if}
	{#if s.last_error}
		<p class="flex items-center gap-1.5 text-xs text-alarm"><TriangleAlert class="size-3.5" /> {s.last_error}</p>
	{/if}
</div>
