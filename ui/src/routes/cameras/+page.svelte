<script lang="ts">
	import { tinker, RpcError, fmt } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Camera, RefreshCw, SlidersHorizontal } from '@lucide/svelte';

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

	function toggle(id: string, o: string) {
		const cur = overlays[id] ?? [];
		overlays[id] = cur.includes(o) ? cur.filter((x) => x !== o) : [...cur, o];
	}

	function streamUrl(c: CameraInfo): string {
		const ov = (overlays[c.id] ?? []).join(',');
		return `${c.stream_url}?${ov ? `overlay=${ov}&` : ''}k=${streamKey}`;
	}

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
			message = r.problems.length ? r.problems.join('; ') : `${c.id}: ${name} = ${value} (not saved: add it to the camera's controls in the configuration to keep it)`;
			await loadControls(c);
		} catch (e) {
			message = e instanceof RpcError ? e.message : String(e);
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

{#if message}<p class="mb-3 text-sm text-muted-foreground">{message}</p>{/if}

<div class="grid gap-4 xl:grid-cols-2">
	{#each cameras as c (c.id)}
		{@const s = st(c.id)}
		<Card.Card class="gap-2 py-3">
			<Card.CardHeader class="px-3">
				<Card.CardTitle class="flex flex-wrap items-center gap-2 text-sm">
					<Camera class="size-4" />
					{c.name}
					<Badge variant="outline" class="font-mono">camera/{c.id}</Badge>
					<Badge variant="secondary">{c.type}</Badge>
					<Badge variant="outline">looking {c.looking}{c.head ? ` · on ${c.head}` : ''}</Badge>
				</Card.CardTitle>
			</Card.CardHeader>
			<Card.CardContent class="flex flex-col gap-2 px-3">
				<div class="overflow-hidden rounded-md bg-black">
					<img src={streamUrl(c)} alt="camera {c.id}" class="mx-auto block max-h-[60vh] w-full object-contain" />
				</div>
				<div class="flex flex-wrap items-center gap-1 text-xs">
					{#each OVERLAYS as o (o)}
						<button onclick={() => toggle(c.id, o)}>
							<Badge variant={(overlays[c.id] ?? []).includes(o) ? 'default' : 'outline'}>{o}</Badge>
						</button>
					{/each}
					<span class="ml-auto flex gap-1">
						<Button variant="outline" size="sm" onclick={() => snapshot(c)}>Snapshot</Button>
						<Button variant="outline" size="sm" onclick={() => reopen(c)}><RefreshCw class="size-3.5" /> Reopen</Button>
						{#if c.type === 'v4l2'}
							<Button variant="outline" size="sm" onclick={() => loadControls(c)}><SlidersHorizontal class="size-3.5" /> Controls</Button>
						{/if}
					</span>
				</div>
				<div class="grid grid-cols-2 gap-x-4 font-mono text-xs text-muted-foreground sm:grid-cols-4">
					<span>{s.width ?? '–'}×{s.height ?? '–'}</span>
					<span>{fmt(s.fps, 1)} fps</span>
					<span>viewers {fmt(s.viewers)}</span>
					<span>{s.streaming ? 'streaming' : 'closed'}</span>
					{#if s.location}
						{@const l = s.location as { x?: number; y?: number; z?: number }}
						<span class="col-span-2">X {fmt(l.x)} Y {fmt(l.y)}{l.z !== undefined ? ` Z ${fmt(l.z)}` : ''}</span>
					{/if}
					{#if s.units_per_pixel}
						{@const u = s.units_per_pixel as { x: number; y: number }}
						<span class="col-span-2">{u.x} × {u.y} mm/px</span>
					{/if}
					{#if s.device}<span class="col-span-4">{s.device} (by {s.matched_by})</span>{/if}
				</div>
				{#if s.last_error}<p class="text-xs text-destructive">{s.last_error}</p>{/if}
				{#if controls[c.id]}
					{@const ctl = controls[c.id]}
					{#if typeof ctl === 'string'}
						<p class="text-xs text-destructive">{ctl}</p>
					{:else}
						<Table.Table>
							<Table.TableBody>
								{#each ctl as k (k.name)}
									<Table.TableRow>
										<Table.TableCell class="font-mono text-xs">{k.name}</Table.TableCell>
										<Table.TableCell class="text-xs text-muted-foreground">{k.type ?? ''} {k.min !== undefined ? `${k.min}..${k.max}` : ''}</Table.TableCell>
										<Table.TableCell class="text-xs">
											<input
												type="number"
												class="w-24 rounded border bg-background px-1"
												value={k.value}
												min={k.min}
												max={k.max}
												step={k.step}
												onchange={(e) => setControl(c, k.name, Number((e.target as HTMLInputElement).value))}
											/>
										</Table.TableCell>
									</Table.TableRow>
								{/each}
							</Table.TableBody>
						</Table.Table>
					{/if}
				{/if}
			</Card.CardContent>
		</Card.Card>
	{/each}
</div>

<Card.Card class="mt-4 gap-2 py-3">
	<Card.CardHeader class="flex flex-row items-center px-3">
		<Card.CardTitle class="text-sm">Video devices on the server</Card.CardTitle>
		<Button variant="outline" size="sm" class="ml-auto" onclick={loadDevices}>Scan</Button>
	</Card.CardHeader>
	<Card.CardContent class="px-3 text-xs">
		{#if devices === null}
			<p class="text-muted-foreground">Scan to list USB cameras with what a camera's <code>device:</code> selector can match.</p>
		{:else if devices.length === 0}
			<p class="text-muted-foreground">No video devices.</p>
		{:else}
			<Table.Table>
				<Table.TableHeader>
					<Table.TableRow>
						<Table.TableHead>Path</Table.TableHead>
						<Table.TableHead>Name</Table.TableHead>
						<Table.TableHead>usb_id</Table.TableHead>
						<Table.TableHead>usb_serial</Table.TableHead>
						<Table.TableHead>Stable links</Table.TableHead>
					</Table.TableRow>
				</Table.TableHeader>
				<Table.TableBody>
					{#each devices as d (d.path)}
						<Table.TableRow class={d.capture ? '' : 'opacity-50'}>
							<Table.TableCell class="font-mono">{d.path}{d.capture ? '' : ' (metadata)'}</Table.TableCell>
							<Table.TableCell>{d.name ?? ''}</Table.TableCell>
							<Table.TableCell class="font-mono">{d.usb_id ?? ''}</Table.TableCell>
							<Table.TableCell class="font-mono">{d.usb_serial ?? ''}</Table.TableCell>
							<Table.TableCell class="font-mono">{d.links.join(' ')}</Table.TableCell>
						</Table.TableRow>
					{/each}
				</Table.TableBody>
			</Table.Table>
		{/if}
	</Card.CardContent>
</Card.Card>
