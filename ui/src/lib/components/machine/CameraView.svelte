<script lang="ts">
	// Live view of one camera. On a head-mounted, down-looking camera with units_per_pixel, Shift+click moves the
	// camera so that the clicked point comes under the crosshair (as OpenPnP's camera view; a plain click does nothing).
	import { tinker, fmt, idOf } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import StreamImg from './StreamImg.svelte';
	import { correctionText, describeOrientation, type Orientation } from '#lib/config/orientation.js';
	import LowBandwidthToggle from './LowBandwidthToggle.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { MousePointerClick, Crosshair, Ruler, LocateFixed, Check, X, LoaderCircle, RotateCw, CameraOff } from '@lucide/svelte';

	let {
		id,
		headMounted = false,
		head = null,
		tool = null
	}: { id: string; headMounted?: boolean; head?: string | null; tool?: string | null } = $props();

	// where the camera's settings are in the configuration
	const cfgPath = $derived(head ? `heads.${head}.cameras.${id}` : `cameras.${id}`);

	const OVERLAYS = ['crosshair', 'scale', 'grid', 'info'] as const;
	let overlays = $state<string[]>(['crosshair', 'scale']);

	const s = $derived(tinker.status[`camera/${id}`] ?? {});
	const upp = $derived(s.units_per_pixel as { x: number; y: number } | null | undefined);
	const loc = $derived((s.location ?? {}) as { x?: number; y?: number; z?: number });
	// Shift+click centres what was clicked (as OpenPnP): a head camera moves there; on an up-looking machine camera
	// the selected nozzle moves (needs a nozzle selected)
	const movable = $derived(
		!!upp &&
			tinker.status['machine']?.homed === true &&
			((headMounted && s.looking === 'down') || (!headMounted && s.looking === 'up' && !!tool?.startsWith('nozzle/')))
	);

	let cursor = $state<{ x: number; y: number } | null>(null);

	// the camera can't deliver pictures: shown in place of the picture, like a monitor's "no signal"
	const unavailable = $derived(!!s.last_error && !s.streaming);
	const errorTitle = $derived(
		String(s.last_error ?? '').startsWith('No camera') ? 'Camera not connected' : 'Camera not available'
	);
	let retrying = $state(false);
	async function retry() {
		retrying = true;
		try {
			await tinker.act('camera.reopen', { id });
		} finally {
			retrying = false;
		}
	}

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
			field: `${cfgPath}.location`,
			from: 'location',
			args: { object: nozzle }
		});
		if (r) captured = r.value;
	}

	type UppResult = {
		value: { x: number; y: number };
		details: Orientation & { field_of_view_mm: number[]; match: number[] };
	};
	let upp_result = $state<UppResult | null>(null);
	const measuring = $derived(tinker.isBusy('config.capture'));

	// head camera looking down: it moves itself over the board (no nozzle needed)
	const headDown = $derived(headMounted && s.looking === 'down');

	async function measureUpp() {
		if (!headDown && !nozzle) return;
		upp_result = await tinker.act<UppResult>('config.capture', {
			field: `${cfgPath}.units_per_pixel`,
			from: 'units_per_pixel',
			args: headDown ? { camera: id, distance: 1.0 } : { camera: id, object: nozzle, distance: 1.0 }
		});
	}

	const orientationOff = $derived(!!upp_result && upp_result.details.needs_correction);

	async function saveUpp(fixOrientation = false) {
		if (!upp_result) return;
		const v = upp_result.value;
		const d = upp_result.details;
		const changes: Record<string, unknown>[] = [
			{ op: 'set', path: `${cfgPath}.units_per_pixel.x`, value: v.x },
			{ op: 'set', path: `${cfgPath}.units_per_pixel.y`, value: v.y }
		];
		if (fixOrientation) {
			// the server worked out the whole transform that puts the picture right (a turn, and a flip only for a
			// real mirror image): set it as a whole, not as changes on top of the old one
			const c = d.correction;
			changes.push(
				{ op: 'set', path: `${cfgPath}.transform.rotate`, value: c.rotate },
				{ op: 'set', path: `${cfgPath}.transform.flip_x`, value: c.flip_x },
				{ op: 'set', path: `${cfgPath}.transform.flip_y`, value: c.flip_y }
			);
		}
		const r = await tinker.act('config.update', { changes });
		if (r) upp_result = null;
	}

	type TipCal = { runout: number; phase: number; axis_offset: { x: number; y: number }; residual: number; angles: number; points: number[][] };
	let tipCal = $state<TipCal | null>(null);
	const calibrating = $derived(tinker.isBusy('nozzle.calibrate_tip'));
	async function calibrateTip() {
		if (!nozzle) return;
		tipCal = await tinker.act<TipCal>('nozzle.calibrate_tip', { nozzle: idOf(nozzle), camera: id, angles: 12 }, 300000);
		if (tipCal) tinker.notify('ok', `${idOf(nozzle)} tip runout ${tipCal.runout.toFixed(3)} mm at ${tipCal.phase.toFixed(0)}°, saved`);
		if (tipCal && !(tipCal as TipCal & { compensated?: boolean }).compensated)
			tinker.notify('warn', `Not compensated yet: as in OpenPnP, turn on calibration for the tip (Config → Nozzle tips → Calibration → Enabled)`);
	}
	// plot: measured tip positions (µm) and the fitted circle
	const plot = $derived.by(() => {
		if (!tipCal) return null;
		const c = tipCal.axis_offset;
		const pts = tipCal.points.map(([, x, y]) => [x, y]);
		const span = Math.max(tipCal.runout * 1.4, ...pts.map(([x, y]) => Math.max(Math.abs(x - c.x), Math.abs(y - c.y)) * 1.2), 0.02);
		const sx = (x: number) => 60 + ((x - c.x) / span) * 52;
		const sy = (y: number) => 60 - ((y - c.y) / span) * 52;
		return { pts: pts.map(([x, y]) => [sx(x), sy(y)]), r: (tipCal.runout / span) * 52 };
	});

	async function saveCapture() {
		if (!captured) return;
		const changes = (['x', 'y', 'z'] as const).map((k) => ({ op: 'set', path: `${cfgPath}.location.${k}`, value: captured![k] }));
		const r = await tinker.act('config.update', { changes });
		if (r) captured = null;
	}

	function toMachine(e: MouseEvent): { x: number; y: number } | null {
		const img = e.currentTarget as HTMLImageElement;
		// units_per_pixel is per camera pixel: use the camera's frame size, not the picture's (a low-bandwidth stream
		// is downscaled)
		const w = (s.width as number) || img.naturalWidth;
		const h = (s.height as number) || img.naturalHeight;
		if (!upp || !w || loc.x === undefined || loc.y === undefined) return null;
		const r = img.getBoundingClientRect();
		const px = ((e.clientX - r.left) / r.width) * w;
		const py = ((e.clientY - r.top) / r.height) * h;
		return {
			x: loc.x + (px - w / 2) * upp.x,
			y: loc.y - (py - h / 2) * upp.y
		};
	}

	function onMove(e: MouseEvent) {
		cursor = toMachine(e);
	}

	function onClick(e: MouseEvent) {
		// as OpenPnP: Shift+click moves; a plain click does nothing (a stray click mustn't move the head)
		if (!movable) return;
		if (!e.shiftKey) {
			tinker.notify('info', 'Hold Shift and click to move there (as in OpenPnP: a plain click never moves the machine)');
			return;
		}
		const target = toMachine(e);
		if (target) tinker.act('camera.center_on', { camera: id, x: target.x, y: target.y, nozzle: headMounted ? null : tool });
	}

</script>

<div class="flex flex-col gap-3">
	<div class="relative overflow-hidden rounded-lg bg-black ring-1 ring-black/10">
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
		<StreamImg
			camera={id}
			overlay={overlays.join(',')}
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
		{#if unavailable}
			<div class="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-neutral-950 px-8 text-center">
				<CameraOff class="size-8 text-white/35" />
				<p class="text-sm font-medium text-white/85">{errorTitle}</p>
				<p class="line-clamp-3 max-w-md text-xs leading-relaxed text-white/50" title={String(s.last_error)}>{s.last_error}</p>
				<Button variant="outline" size="sm" class="mt-1 border-white/20 bg-white/5 text-white/85 hover:bg-white/10 hover:text-white" disabled={retrying} onclick={retry}>
					<RotateCw class={retrying ? 'animate-spin' : ''} /> Try again
				</Button>
			</div>
		{/if}
		{#if cursor}
			<span class="num pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-primary px-2 py-0.5 text-[11px] font-semibold text-primary-foreground">
				{movable ? 'Shift+click → ' : ''}X {fmt(cursor.x, 2)} Y {fmt(cursor.y, 2)}
			</span>
		{/if}
	</div>

	<div class="flex flex-wrap items-center gap-2">
		<ToggleGroup.Root type="multiple" variant="outline" size="sm" bind:value={overlays} aria-label="overlays">
			{#each OVERLAYS as o (o)}
				<ToggleGroup.Item value={o} class="capitalize {segmentItem}">{o}</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
		<LowBandwidthToggle />
		{#if movable}
			<span class="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
				<MousePointerClick class="size-3.5" /> <kbd class="kbd">Shift</kbd>+click in the image to move there
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
				title="Move {idOf(nozzle)} to this camera's location (safe Z, then down to its focus Z), then 0.5 and 1 mm in X and Y, and measure how far the image moves">
				{#if measuring}<LoaderCircle class="animate-spin" /> Measuring…{:else}<Ruler /> Measure mm/px{/if}
			</Button>
			<Button variant="outline" size="sm" disabled={!homed || calibrating} onclick={calibrateTip}
				title="Turn the empty nozzle over this camera at its focus height and measure the tip's runout (saved, then compensated)">
				{#if calibrating}<LoaderCircle class="animate-spin" /> Calibrating…{:else}<RotateCw /> Tip runout{/if}
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
	{/if}
	{#if headDown}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="eyebrow mr-1">Calibrate</span>
			<Button variant="outline" size="sm" disabled={!homed || measuring} onclick={measureUpp}
				title="Move this camera 0.5 and 1 mm in X and Y over the board under it (any detail, in focus) and measure how far the picture moves">
				{#if measuring}<LoaderCircle class="animate-spin" /> Measuring…{:else}<Ruler /> Measure mm/px{/if}
			</Button>
			<span class="text-xs text-muted-foreground">over a board with some detail, in focus</span>
		</div>
	{/if}
	{#if upp_result}
		{@const d = upp_result.details}
		<div class="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
			<span class="font-medium">Measured</span>
			<span class="num">{fmt(upp?.x, 5)} × {fmt(upp?.y, 5)} → <b>{upp_result.value.x.toFixed(5)} × {upp_result.value.y.toFixed(5)}</b> mm/px</span>
			<span class="text-muted-foreground">view {d.field_of_view_mm[0]} × {d.field_of_view_mm[1]} mm</span>
			{#if d.needs_correction}<span class="text-warn">The picture is {describeOrientation(d)}: fix sets {correctionText(d)}</span>{/if}
			<span class="ml-auto flex gap-1">
				{#if orientationOff}<Button size="sm" onclick={() => saveUpp(true)}><Check /> Save + fix orientation</Button>{/if}
				<Button size="sm" variant={orientationOff ? 'outline' : 'default'} onclick={() => saveUpp(false)}><Check /> Save{orientationOff ? ' mm/px only' : ''}</Button>
				<Button variant="ghost" size="sm" onclick={() => (upp_result = null)}><X /> Cancel</Button>
			</span>
		</div>
	{/if}
	{#if tipCal && plot}
		<div class="flex items-center gap-4 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
			<svg viewBox="0 0 120 120" class="size-24 shrink-0">
				<line x1="0" y1="60" x2="120" y2="60" class="stroke-border" />
				<line x1="60" y1="0" x2="60" y2="120" class="stroke-border" />
				<circle cx="60" cy="60" r={plot.r} class="fill-none stroke-primary" stroke-width="1.5" />
				{#each plot.pts as [x, y], i (i)}<circle cx={x} cy={y} r="2.5" class="fill-warn" />{/each}
			</svg>
			<div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
				<span class="text-muted-foreground">Runout</span><span class="num font-semibold">{tipCal.runout.toFixed(3)} mm</span>
				<span class="text-muted-foreground">Phase</span><span class="num">{tipCal.phase.toFixed(1)}°</span>
				<span class="text-muted-foreground">Axis vs camera</span><span class="num">X {tipCal.axis_offset.x.toFixed(3)} Y {tipCal.axis_offset.y.toFixed(3)} mm</span>
				<span class="text-muted-foreground">Fit</span><span class="num">{tipCal.angles} angles, residual {tipCal.residual.toFixed(3)} mm</span>
			</div>
			<Button variant="ghost" size="sm" class="ml-auto self-start" onclick={() => (tipCal = null)}><X /></Button>
		</div>
	{/if}
</div>
