<script lang="ts">
	import { tinker, RpcError, fmt } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { segmentItem } from '#lib/styles.js';
	import StreamImg from '#lib/components/machine/StreamImg.svelte';
	import LowBandwidthToggle from '#lib/components/machine/LowBandwidthToggle.svelte';
	import { Camera, RefreshCw, SlidersHorizontal, Aperture, Usb, ScanSearch, Info, TriangleAlert, Save } from '@lucide/svelte';

	type CameraInfo = {
		id: string;
		name: string;
		type: string;
		looking: string;
		head: string | null;
		stream_url: string;
		snapshot_url: string;
	};
	type Device = {
		path: string;
		name: string | null;
		capture: boolean;
		usb_id: string | null;
		usb_serial: string | null;
		product: string | null;
		links: string[];
	};
	type Control = { name: string; type?: string; min?: number; max?: number; step?: number; default?: number; value?: number };

	const OVERLAYS = ['crosshair', 'scale', 'grid', 'info'] as const;

	let cameras = $state<CameraInfo[]>([]);
	let overlays = $state<Record<string, string[]>>({});
	let devices = $state<Device[] | null>(null);
	let controls = $state<Record<string, Control[] | string>>({});
	let message = $state<string | null>(null);
	// controls changed in this page but not saved to the configuration yet, per camera
	let pending = $state<Record<string, Record<string, number>>>({});
	// bumped to force <img> to reconnect after a reopen
	let streamKey = $state(0);

	$effect(() => {
		void tinker.configRevision;
		if (tinker.connection !== 'connected') return;
		tinker
			.call<CameraInfo[]>('camera.list')
			.then((list) => {
				cameras = list;
				for (const c of list) overlays[c.id] ??= ['crosshair'];
			})
			.catch((e) => (message = String(e)));
	});



	const st = (id: string) => tinker.status[`camera/${id}`] ?? {};

	async function snapshot(c: CameraInfo) {
		try {
			const r = await tinker.call<{ url: string }>('camera.snapshot', { id: c.id, overlay: (overlays[c.id] ?? []).join(',') || undefined });
			window.open(r.url, '_blank');
		} catch (e) {
			message = e instanceof RpcError ? e.message : String(e);
		}
	}

	async function reopen(c: CameraInfo) {
		try {
			await tinker.call('camera.reopen', { id: c.id });
			streamKey++;
		} catch (e) {
			message = e instanceof RpcError ? e.message : String(e);
		}
	}

	async function loadControls(c: CameraInfo) {
		try {
			controls[c.id] = await tinker.call<Control[]>('camera.controls', { id: c.id });
		} catch (e) {
			controls[c.id] = e instanceof RpcError ? e.message : String(e);
		}
	}

	async function setControl(c: CameraInfo, name: string, value: number) {
		try {
			const r = await tinker.call<{ problems: string[] }>('camera.set_controls', { id: c.id, controls: { [name]: value } });
			if (r.problems.length) {
				message = r.problems.join('; ');
			} else {
				pending[c.id] = { ...(pending[c.id] ?? {}), [name]: value };
				message = `${c.id}: ${name} = ${value}, set now; Save keeps it in the configuration`;
			}
			await loadControls(c);
		} catch (e) {
			message = e instanceof RpcError ? e.message : String(e);
		}
	}

	async function saveControls(c: CameraInfo) {
		const base = c.head ? `heads.${c.head}.cameras.${c.id}.controls` : `cameras.${c.id}.controls`;
		const changes = Object.entries(pending[c.id] ?? {}).map(([name, value]) => ({ op: 'set', path: `${base}.${name}`, value }));
		if (!changes.length) return;
		const r = await tinker.act('config.update', { changes });
		if (r) {
			message = `${c.id}: saved ${changes.length} control(s) to the configuration`;
			delete pending[c.id];
		}
	}

	async function loadDevices() {
		try {
			devices = await tinker.call<Device[]>('camera.devices');
		} catch (e) {
			message = String(e);
		}
	}
</script>

{#if message}
	<div class="mb-4 flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
		<Info class="size-4 text-primary" /> {message}
	</div>
{/if}

<div class="grid items-start gap-4 xl:grid-cols-2">
	{#each cameras as c (c.id)}
		{@const s = st(c.id)}
		{@const l = (s.location ?? {}) as { x?: number; y?: number; z?: number }}
		{@const u = s.units_per_pixel as { x: number; y: number } | null}
		<Card.Root class="gap-3">
			<Card.Header class="flex items-center gap-2">
				<Camera class="size-4 text-muted-foreground" />
				<Card.Title class="text-sm">{c.name}</Card.Title>
				<Badge variant="outline" class="font-mono">{c.id}</Badge>
				<Card.Action class="flex gap-1">
					<Button variant="ghost" size="icon-sm" title="Snapshot (opens the frame)" onclick={() => snapshot(c)}><Aperture /></Button>
					<Button variant="ghost" size="icon-sm" title="Reopen the device" onclick={() => reopen(c)}><RefreshCw /></Button>
					{#if c.type === 'v4l2'}
						<Button variant="ghost" size="icon-sm" title="Device controls" onclick={() => loadControls(c)}><SlidersHorizontal /></Button>
					{/if}
				</Card.Action>
			</Card.Header>
			<Card.Content class="flex flex-col gap-3">
				<div class="relative overflow-hidden rounded-lg bg-black">
					{#key streamKey}
						<StreamImg camera={c.id} overlay={(overlays[c.id] ?? []).join(',')} alt="camera {c.id}" class="mx-auto block aspect-[4/3] max-h-[60vh] w-full object-contain" />
					{/key}
					<span class="num absolute top-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] text-white/90">
						{s.width ?? '–'}×{s.height ?? '–'} · {fmt(s.fps, 0)} fps
					</span>
				</div>
				<div class="flex flex-wrap items-center gap-2">
					<ToggleGroup.Root type="multiple" variant="outline" size="sm" bind:value={overlays[c.id]} aria-label="overlays">
						{#each OVERLAYS as o (o)}
							<ToggleGroup.Item value={o} class="capitalize {segmentItem}">{o}</ToggleGroup.Item>
						{/each}
					</ToggleGroup.Root>
					<LowBandwidthToggle />
				</div>
				<dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-xs">
					<dt class="text-muted-foreground">Type</dt><dd>{c.type} · looking {c.looking}{c.head ? ` · on head ${c.head}` : ''}</dd>
					<dt class="text-muted-foreground">Location</dt>
					<dd class="num">{l.x !== undefined ? `X ${fmt(l.x)}  Y ${fmt(l.y)}${l.z !== undefined ? `  Z ${fmt(l.z)}` : ''}` : '–'}</dd>
					<dt class="text-muted-foreground">mm / pixel</dt><dd class="num">{u ? `${u.x} × ${u.y}` : 'not set'}</dd>
					{#if s.device}<dt class="text-muted-foreground">Device</dt><dd class="num">{s.device} <span class="text-muted-foreground">(by {s.matched_by})</span></dd>{/if}
					<dt class="text-muted-foreground">Status</dt>
					<dd>
						{#if s.streaming}<Badge variant="ok">streaming</Badge>{:else}<Badge variant="outline">closed</Badge>{/if}
						<span class="ml-1 text-muted-foreground">{fmt(s.viewers)} viewer{s.viewers === 1 ? '' : 's'}</span>
					</dd>
				</dl>
				{#if s.last_error}
					<p class="flex items-center gap-1.5 rounded-md bg-alarm/10 px-2.5 py-1.5 text-xs text-alarm"><TriangleAlert class="size-3.5 shrink-0" /> {s.last_error}</p>
				{/if}
				{#if controls[c.id]}
					{@const ctl = controls[c.id]}
					<div class="rounded-lg border">
						<div class="flex items-center gap-2 border-b px-3 py-2">
							<SlidersHorizontal class="size-3.5 text-muted-foreground" />
							<span class="text-xs font-semibold">Device controls</span>
							{#if Object.keys(pending[c.id] ?? {}).length}
								<span class="truncate text-xs text-warn">changed: {Object.entries(pending[c.id]).map(([k, v]) => `${k}=${v}`).join(', ')}</span>
								<Button size="sm" class="ml-auto" onclick={() => saveControls(c)}><Save /> Save to configuration</Button>
							{/if}
						</div>
						{#if typeof ctl === 'string'}
							<p class="p-3 text-xs text-alarm">{ctl}</p>
						{:else}
							<Table.Root>
								<Table.Body>
									{#each ctl as k (k.name)}
										<Table.Row>
											<Table.Cell class="font-mono text-xs">{k.name}</Table.Cell>
											<Table.Cell class="num text-xs text-muted-foreground">{k.min !== undefined ? `${k.min} … ${k.max}` : k.type ?? ''}</Table.Cell>
											<Table.Cell class="w-28 text-right">
												{#if k.type === 'bool'}
													<Switch checked={k.value === 1} aria-label={k.name} onCheckedChange={(v) => setControl(c, k.name, v ? 1 : 0)} />
												{:else}
													<Input
														type="number"
														class="ml-auto h-7 w-24 text-right font-mono text-xs"
														value={k.value}
														min={k.min}
														max={k.max}
														step={k.step}
														aria-label={k.name}
														onchange={(e) => setControl(c, k.name, Number((e.target as HTMLInputElement).value))}
													/>
												{/if}
											</Table.Cell>
										</Table.Row>
									{/each}
								</Table.Body>
							</Table.Root>
						{/if}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	{/each}
</div>

<Card.Root class="mt-4 gap-3">
	<Card.Header class="flex items-center gap-2">
		<Usb class="size-4 text-muted-foreground" />
		<Card.Title class="text-sm">Video devices on the server</Card.Title>
		<Card.Action><Button variant="outline" size="sm" onclick={loadDevices}><ScanSearch /> Scan</Button></Card.Action>
	</Card.Header>
	<Card.Content class="text-xs">
		{#if devices === null}
			<p class="text-muted-foreground">Scan to list USB cameras with what a camera's <code class="font-mono">device:</code> selector can match.</p>
		{:else if devices.length === 0}
			<p class="text-muted-foreground">No video devices.</p>
		{:else}
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Path</Table.Head>
						<Table.Head>Name</Table.Head>
						<Table.Head>usb_id</Table.Head>
						<Table.Head>usb_serial</Table.Head>
						<Table.Head>Stable links</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each devices as d (d.path)}
						<Table.Row class={d.capture ? '' : 'opacity-50'}>
							<Table.Cell class="font-mono">{d.path}{d.capture ? '' : ' (metadata)'}</Table.Cell>
							<Table.Cell>{d.name ?? ''}</Table.Cell>
							<Table.Cell class="font-mono">{d.usb_id ?? ''}</Table.Cell>
							<Table.Cell class="font-mono">{d.usb_serial ?? ''}</Table.Cell>
							<Table.Cell class="max-w-md truncate font-mono" title={d.links.join(' ')}>{d.links.join(' ')}</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		{/if}
	</Card.Content>
</Card.Root>
