<script lang="ts">
	// Buttons inside a settings group that measure a value on the machine (as OpenPnP's capture buttons) or test the
	// object. Measured values go into the form only: Apply saves them.
	import { tinker } from '#lib/api/client.svelte.js';
	import type { ActionId, LayoutContext } from '#lib/config/layouts.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import DevicePicker from './DevicePicker.svelte';
	import { Crosshair, Info, Power, PowerOff, Ruler } from '@lucide/svelte';

	type Obj = Record<string, unknown>;

	let {
		action,
		ctx,
		config,
		saved,
		set
	}: {
		action: ActionId;
		ctx: LayoutContext;
		config: Obj;
		/** the object as saved (tests use the running machine, which has the saved settings) */
		saved: boolean;
		set: (path: string, v: unknown) => void;
	} = $props();

	const id = $derived(ctx.path.split('.').pop() ?? '');
	const nozzles = $derived(
		Object.values((config.heads as Obj | undefined) ?? {}).flatMap((h) => Object.keys(((h as Obj).nozzles as Obj | undefined) ?? {}))
	);
	let nozzle = $state('');
	$effect(() => {
		if (!nozzle && nozzles.length) nozzle = nozzles[0];
	});
	let distance = $state(1);
	let busy = $state(false);
	let result = $state<{ ok: boolean; text: string } | null>(null);
	let testValue = $state('');

	const ready = $derived(tinker.status['machine']?.enabled === true && tinker.status['machine']?.homed === true);

	async function run(fn: () => Promise<string>) {
		busy = true;
		result = null;
		try {
			result = { ok: true, text: await fn() };
		} catch (e) {
			result = { ok: false, text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}

	const captureLocation = () =>
		run(async () => {
			const r = await tinker.call<{ value: { x: number; y: number; z: number } }>('config.capture', { field: 'location', args: { object: `nozzle/${nozzle}` } });
			for (const a of ['x', 'y', 'z'] as const) set(`location.${a}`, r.value[a]);
			return `Captured X ${r.value.x}, Y ${r.value.y}, Z ${r.value.z} from ${nozzle}. Apply to save it.`;
		});

	// as OpenPnP's Issues & Solutions: the measured mirror and rotation can be set right away
	let orientation = $state<{ mirroredX: boolean; mirroredY: boolean; rotation: number } | null>(null);
	function applyOrientation() {
		if (!orientation) return;
		const t = (ctx.value.transform ?? {}) as Obj;
		// mirrored in Y (machine +Y moves the picture down): flip about the X axis; in X: about the Y axis
		if (orientation.mirroredY) set('transform.flip_x', !(t.flip_x as boolean));
		if (orientation.mirroredX) set('transform.flip_y', !(t.flip_y as boolean));
		if (Math.abs(orientation.rotation) > 0.05) set('transform.rotate', Math.round((Number(t.rotate ?? 0) - orientation.rotation) * 100) / 100);
		result = { ok: true, text: 'Flip and rotation set from the measurement. Apply, then Measure again to check.' };
		orientation = null;
	}

	const measureScale = () =>
		run(async () => {
			const r = await tinker.call<{ value: { x: number; y: number }; details?: Obj }>('config.capture', {
				field: 'units_per_pixel',
				from: 'units_per_pixel',
				args: { camera: id, object: `nozzle/${nozzle}`, distance }
			});
			set('units_per_pixel.x', r.value.x);
			set('units_per_pixel.y', r.value.y);
			const d = r.details ?? {};
			orientation = { mirroredX: !!d.mirrored_x, mirroredY: !!d.mirrored_y, rotation: Number(d.rotation_deg ?? 0) };
			const extra = Object.entries(d)
				.filter(([, v]) => typeof v !== 'object')
				.map(([k, v]) => `${k.replaceAll('_', ' ')}: ${v}`)
				.join(', ');
			return `Measured ${r.value.x} × ${r.value.y} mm/px with ${nozzle} at the camera's location${extra ? ` (${extra})` : ''}. Check the image is sharp there, then Apply to save it.`;
		});

	// advanced calibration: the saved model, a new one, and putting it to use
	type Model = {
		rms_px: number; points: number; outliers: number; distortion: number[]; tilt_deg: number; rotation_deg: number;
		mirrored: boolean; distance_mm: number; units_per_pixel: number; axis_offset: { x: number; y: number };
		time: number; nozzle: string; active?: boolean;
	};
	let model = $state<Model | null>(null);
	$effect(() => {
		if (action === 'advanced-calibration' && tinker.connection === 'connected')
			tinker.call<Model | null>('camera.calibration', { camera: id }).then((m) => (model = m)).catch(() => (model = null));
	});
	const calibrate = () =>
		run(async () => {
			model = await tinker.call<Model>('camera.calibrate', { camera: id, nozzle: ctx.value.looking === 'down' ? null : nozzle }, 900000);
			return `Calibrated with ${model.points} points, ${model.rms_px.toFixed(2)} px RMS. Saved; "Use it" switches the correction on.`;
		});
	function useModel() {
		if (!model) return;
		set('calibration.enabled', true);
		set('units_per_pixel.x', model.units_per_pixel);
		set('units_per_pixel.y', model.units_per_pixel);
		result = { ok: true, text: 'Correction on and mm/px set from the model. Apply to save.' };
	}
	const forget = () =>
		run(async () => {
			await tinker.call('camera.reset_calibration', { camera: id });
			model = null;
			set('calibration.enabled', undefined);
			return 'Model forgotten. Apply to save the setting.';
		});

	// nozzle tip changes (tool changer, or by hand)
	const tips = $derived(Object.keys((config.nozzle_tips as Obj | undefined) ?? {}));
	const loadedTip = $derived((tinker.status[`nozzle/${id}`]?.tip as string | null | undefined) ?? null);
	let newTip = $state('');
	const changeTip = (manual: boolean) =>
		run(async () => {
			const r = await tinker.call<{ tip: string }>('nozzle.load_tip', { nozzle: id, tip: newTip, manual }, 120000);
			return `${r.tip} loaded on ${id}${manual ? ' (by hand)' : ''}.`;
		});
	const unloadTip = (manual: boolean) =>
		run(async () => {
			await tinker.call('nozzle.unload_tip', { nozzle: id, manual }, 120000);
			return `${id} has no tip now${manual ? ' (taken off by hand)' : ''}.`;
		});

	const actuate = (value: unknown) =>
		run(async () => {
			const r = await tinker.call<{ value: unknown }>('actuator.set', { id, value });
			return `${id} set to ${JSON.stringify(r.value)}.`;
		});
</script>

{#snippet nozzlePicker()}
	<select class="h-8 rounded-md border border-input bg-background px-2 font-mono text-xs" bind:value={nozzle}>
		{#each nozzles as n (n)}<option value={n}>{n}</option>{/each}
	</select>
{/snippet}

<div class="flex flex-col gap-1.5 rounded-lg border border-dashed px-3 py-2">
	{#if action === 'device-picker'}
		<DevicePicker onpick={(d) => set('device', d)} self={ctx.onHead ? `${ctx.path.split('.')[1]}/${id}` : id} />
	{:else if action === 'head-mounted'}
		<p class="flex items-center gap-1.5 text-xs text-muted-foreground">
			<Info class="size-3.5" />
			{#if ctx.onHead}Mounted on head <span class="font-mono">{ctx.path.split('.')[1]}</span>: it moves with the head.{:else}Mounted on the machine: it stays where it is.{/if}
			To mount it elsewhere, add a new camera there and delete this one.
		</p>
	{:else if action === 'capture-camera-location'}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<span>Centre a nozzle over the camera, in focus, then</span>
			{@render nozzlePicker()}
			<Button variant="outline" size="sm" disabled={busy || !ready || !nozzles.length} onclick={captureLocation}><Crosshair /> Capture nozzle location</Button>
		</div>
	{:else if action === 'measure-units-per-pixel'}
		{#if ctx.onHead || ctx.value.looking === 'down'}
			<p class="text-xs text-muted-foreground">Measuring is available for up-looking cameras on the machine. Enter the values by hand (e.g. image a ruler).</p>
		{:else}
			<div class="flex flex-wrap items-center gap-2 text-xs">
				<span>Moves</span>
				{@render nozzlePicker()}
				<span>to the camera's location (safe Z, then down to the focus Z from Position), then</span>
				<input class="h-8 w-16 rounded-md border border-input bg-background px-2 text-right font-mono text-xs" type="number" min="0.1" step="0.1" bind:value={distance} />
				<span>mm in X and Y:</span>
				<Button variant="outline" size="sm" disabled={busy || !ready || !saved || !nozzles.length} onclick={measureScale}><Ruler /> Measure</Button>
			</div>
			{#if ctx.value.location === undefined || [(ctx.value.location as Obj).x, (ctx.value.location as Obj).y, (ctx.value.location as Obj).z].some((v) => v === undefined || v === null)}
				<p class="text-[11px] text-warn">Set the camera's location first (Position tab: X, Y and the Z where the nozzle tip is in focus).</p>
			{:else if !saved}<p class="text-[11px] text-warn">Apply the camera's changes first: measuring uses the saved location.</p>{/if}
			{#if orientation && (orientation.mirroredX || orientation.mirroredY || Math.abs(orientation.rotation) > 0.05)}
				<div class="flex items-center gap-2 text-xs">
					<span class="text-warn">The picture is {[orientation.mirroredX ? 'mirrored in X' : '', orientation.mirroredY ? 'mirrored in Y' : '', Math.abs(orientation.rotation) > 0.05 ? `rotated ${orientation.rotation.toFixed(2)}°` : ''].filter(Boolean).join(', ')}.</span>
					<Button size="sm" variant="outline" onclick={applyOrientation}>Apply orientation</Button>
				</div>
			{/if}
		{/if}
	{:else if action === 'advanced-calibration'}
		{#if ctx.value.looking === 'down' && !ctx.onHead}
			<p class="text-xs text-muted-foreground">Down-looking cameras are calibrated on the head (they move over the fiducials).</p>
		{:else}
			<div class="flex flex-wrap items-center gap-2 text-xs">
				{#if ctx.value.looking === 'down'}
					<span>Moves the camera over the primary, then the secondary calibration fiducial, so each walks {ctx.value.calibration ? ((ctx.value.calibration as Obj).radial_lines ?? 32) : 32} lines out to the edge of the view (a few minutes):</span>
					<Button variant="outline" size="sm" disabled={busy || !ready || !saved} onclick={calibrate}><Ruler /> Calibrate</Button>
				{:else}
					<span>Moves</span>
					{@render nozzlePicker()}
					<span>to the camera's location, then along {ctx.value.calibration ? ((ctx.value.calibration as Obj).radial_lines ?? 32) : 32} lines out to the edge of the view, at two heights (a few minutes):</span>
					<Button variant="outline" size="sm" disabled={busy || !ready || !saved || !nozzles.length} onclick={calibrate}><Ruler /> Calibrate</Button>
				{/if}
			</div>
			{#if !saved}<p class="text-[11px] text-warn">Apply the camera's changes first: calibration uses the saved location and mm/px.</p>{/if}
			{#if model}
				<div class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 rounded-md bg-muted/40 px-3 py-2 text-xs">
					<span class="text-muted-foreground">Fit</span><span class="font-mono">{model.rms_px.toFixed(3)} px RMS · {model.points} points{model.outliers ? `, ${model.outliers} outliers dropped` : ''}</span>
					<span class="text-muted-foreground">Lens</span><span class="font-mono">k1 {model.distortion[0].toFixed(4)} · k2 {model.distortion[1].toFixed(4)}</span>
					<span class="text-muted-foreground">Camera</span><span class="font-mono">{model.distance_mm.toFixed(1)} mm away · tilted {model.tilt_deg.toFixed(2)}° · turned {model.rotation_deg.toFixed(1)}°{model.mirrored ? ' · mirror image' : ''}</span>
					<span class="text-muted-foreground">Optical axis</span><span class="font-mono">X {model.axis_offset.x >= 0 ? '+' : ''}{model.axis_offset.x.toFixed(3)} Y {model.axis_offset.y >= 0 ? '+' : ''}{model.axis_offset.y.toFixed(3)} mm from the location</span>
					<span class="text-muted-foreground">mm/px</span><span class="font-mono">{model.units_per_pixel.toFixed(6)} at the location's Z</span>
					<span class="text-muted-foreground">Status</span><span>{model.active ? 'correcting the image' : 'saved, not in use'} · {new Date(model.time * 1000).toLocaleString()}</span>
				</div>
				<div class="flex gap-2">
					<Button size="sm" variant="outline" disabled={busy} onclick={useModel}>Use it</Button>
					<Button size="sm" variant="ghost" class="text-alarm" disabled={busy} onclick={forget}>Forget</Button>
				</div>
			{/if}
		{/if}
	{:else if action === 'tip-change'}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<span>Loaded now: <span class="font-mono">{loadedTip ?? 'none'}</span>. Change to</span>
			<select class="h-8 rounded-md border border-input bg-background px-2 font-mono text-xs" bind:value={newTip}>
				<option value="">…</option>
				{#each tips as t (t)}<option value={t}>{t}</option>{/each}
			</select>
			<Button variant="outline" size="sm" disabled={busy || !newTip || !ready} onclick={() => changeTip(false)}>Load (tool changer)</Button>
			<Button variant="ghost" size="sm" disabled={busy || !newTip} onclick={() => changeTip(true)}>Changed by hand</Button>
			<Button variant="ghost" size="sm" disabled={busy || !loadedTip} onclick={() => unloadTip(true)}>Taken off by hand</Button>
		</div>
	{:else if action === 'actuator-test'}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<span>Test:</span>
			{#if (ctx.value.value ?? 'boolean') === 'boolean'}
				<Button variant="outline" size="sm" disabled={busy || !saved} onclick={() => actuate(true)}><Power /> On</Button>
				<Button variant="outline" size="sm" disabled={busy || !saved} onclick={() => actuate(false)}><PowerOff /> Off</Button>
			{:else}
				<input class="h-8 w-32 rounded-md border border-input bg-background px-2 font-mono text-xs" placeholder="value" bind:value={testValue} />
				<Button variant="outline" size="sm" disabled={busy || !saved || testValue === ''} onclick={() => actuate(ctx.value.value === 'double' ? Number(testValue) : testValue)}>Set</Button>
			{/if}
			{#if !saved}<span class="text-warn">Apply first: the test uses the saved settings.</span>{/if}
		</div>
	{/if}
	{#if (action === 'capture-camera-location' || action === 'measure-units-per-pixel' || action === 'advanced-calibration') && !ready && !ctx.onHead}
		<p class="text-[11px] text-muted-foreground">Enable and home the machine to use this.</p>
	{/if}
	{#if result}<p class="text-xs {result.ok ? 'text-ok' : 'text-alarm'}">{result.text}</p>{/if}
</div>
