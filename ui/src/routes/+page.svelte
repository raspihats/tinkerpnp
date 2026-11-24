<script lang="ts">
	// Machine page: a dashboard of panels (camera, toolhead, actuators, macros, console, nozzles, axes) in 1–3 columns
	// depending on the width. Panels are moved by dragging their handle and collapsed with their chevron; the layout
	// is remembered in this browser (one per column count), like Mainsail/Fluidd.
	import { tinker, fmt, idOf, kindOf, type Loc } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import { Dashboard } from '#lib/dashboard.svelte.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import CameraView from '#lib/components/machine/CameraView.svelte';
	import JogPanel from '#lib/components/machine/JogPanel.svelte';
	import ConsoleView from '#lib/components/machine/ConsoleView.svelte';
	import DashPanel from '#lib/components/machine/DashPanel.svelte';
	import StreamImg from '#lib/components/machine/StreamImg.svelte';
	import {
		Power,
		PowerOff,
		ShieldAlert,
		LoaderCircle,
		Camera,
		Move,
		ToggleRight,
		Crosshair,
		Ruler,
		Zap,
		SquareTerminal,
		LayoutDashboard,
		Play
	} from '@lucide/svelte';

	type CameraInfo = { id: string; name: string; looking: string; head: string | null };

	const dash = new Dashboard('tinkerui.dashboard.machine', {
		1: [['camera', 'toolhead', 'actuators', 'macros', 'console', 'nozzles', 'axes']],
		2: [
			['camera', 'console', 'nozzles', 'axes'],
			['toolhead', 'actuators', 'macros']
		],
		3: [
			['camera', 'nozzles', 'axes'],
			['toolhead', 'actuators'],
			['macros', 'console']
		]
	});
	const gridCols: Record<number, string> = {
		1: 'grid-cols-1',
		2: 'grid-cols-[minmax(0,1fr)_27rem]',
		3: 'grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)_minmax(0,1fr)]'
	};

	const st = $derived(tinker.status);
	const of = (kind: string) => tinker.objects.filter((n) => kindOf(n) === kind);
	const machine = $derived(st['machine'] ?? {});
	const enabled = $derived(machine.enabled === true);
	const homed = $derived(machine.homed === true);
	const inAlarm = $derived(machine.state === 'alarm');

	let cameras = $state<CameraInfo[]>([]);
	let viewCamera = $state<string>('');
	let distances = $state<number[]>([0.01, 0.1, 1, 10, 100]);
	let liftDefault = $state(true);
	let macros = $state<string[]>([]);

	// cameras, jog settings and macros come from the server; reload when the configuration changes
	$effect(() => {
		void tinker.configRevision;
		if (tinker.connection !== 'connected') return;
		tinker
			.call<CameraInfo[]>('camera.list')
			.then((list) => {
				cameras = list;
				if (!viewCamera || !list.some((c) => c.id === viewCamera)) {
					viewCamera = (list.find((c) => c.head && c.looking === 'down') ?? list[0])?.id ?? '';
				}
			})
			.catch(() => {});
		tinker
			.call<{ config: { jog?: { distances?: number[]; safe_z?: string }; macros?: Record<string, unknown> } }>('config.get', { path: 'machine' })
			.then((r) => {
				const jog = r.config?.jog;
				if (jog?.distances?.length) distances = jog.distances;
				liftDefault = jog?.safe_z !== 'never';
				macros = Object.keys(r.config?.macros ?? {});
			})
			.catch(() => {});
	});

	// jog targets: nozzles and head-mounted cameras
	const tools = $derived([...of('nozzle'), ...cameras.filter((c) => c.head).map((c) => `camera/${c.id}`)]);
	// a selected head camera is also shown
	$effect(() => {
		const sel = machine.selected_tool as string | undefined;
		if (sel?.startsWith('camera/')) viewCamera = idOf(sel);
	});

	const busy = (...m: string[]) => tinker.isBusy(...m);
	const loc = (v: unknown): Loc => (v as Loc) ?? {};
	const cam = $derived(cameras.find((c) => c.id === viewCamera));
	const others = $derived(cameras.filter((c) => c.id !== viewCamera));

	let runningMacro = $state<string | null>(null);
	async function runMacro(name: string) {
		runningMacro = name;
		const r = await tinker.act('machine.run_macro', { name });
		runningMacro = null;
		if (r) tinker.notify('ok', `Macro "${name}" done`);
	}

	// drag and drop between columns
	let dropAt = $state<string | null>(null);
	function onDrop(e: DragEvent, col: number, before: string | null) {
		e.preventDefault();
		const id = e.dataTransfer?.getData('text/plain') || dash.dragging;
		if (id && id !== before) dash.move(id, col, before);
		dash.dragging = null;
		dropAt = null;
	}
	function onOver(e: DragEvent, key: string) {
		if (!dash.dragging) return;
		e.preventDefault();
		dropAt = key;
	}

	const titles: Record<string, string> = {
		camera: 'Camera',
		toolhead: 'Toolhead',
		actuators: 'Actuators',
		macros: 'Macros',
		console: 'Console',
		nozzles: 'Nozzles',
		axes: 'Axes'
	};
	const icons = { camera: Camera, toolhead: Move, actuators: ToggleRight, macros: Zap, console: SquareTerminal, nozzles: Crosshair, axes: Ruler };
</script>

<div class="flex flex-col gap-4">
	<!-- machine power and alarm -->
	<div class="flex flex-wrap items-center gap-2">
		{#if !enabled}
			<Button size="lg" class="px-4" disabled={busy('machine.enable')} onclick={() => tinker.act('machine.enable')}>
				{#if busy('machine.enable')}<LoaderCircle class="animate-spin" />{:else}<Power />{/if} Enable
			</Button>
			<span class="text-sm text-muted-foreground">Enabling resets the controller and homes the machine.</span>
		{:else}
			<Button size="lg" variant="outline" onclick={() => tinker.act('machine.disable')}><PowerOff /> Disable</Button>
			{#if inAlarm}
				<Button size="lg" variant="outline" class="border-alarm/50 text-alarm" onclick={() => tinker.act('machine.clear_alarm')}>
					<ShieldAlert /> Clear alarm
				</Button>
			{/if}
			{#if st['motion']?.safe_z === false}<Badge variant="warn">a nozzle is below safe Z</Badge>{/if}
		{/if}
		<Button variant="ghost" size="sm" class="ml-auto text-muted-foreground" onclick={() => dash.reset()} title="Put the panels back where they started">
			<LayoutDashboard /> Reset layout
		</Button>
	</div>

	<!-- dashboard -->
	<div bind:clientWidth={dash.width} class="grid items-start gap-4 {gridCols[dash.count]}">
		{#each dash.columns as col, ci (ci)}
			<div class="flex min-w-0 flex-col gap-4">
				{#each col as id (id)}
					<div
						role="region"
						aria-label={titles[id]}
						class="rounded-xl transition-shadow {dropAt === id && dash.dragging !== id ? 'ring-2 ring-primary ring-offset-2 ring-offset-background' : ''}"
						ondragover={(e) => onOver(e, id)}
						ondragleave={() => dropAt === id && (dropAt = null)}
						ondrop={(e) => onDrop(e, ci, id)}
					>
						{@render panel(id)}
					</div>
				{/each}
				{#if dash.dragging}
					<div
						role="region"
						aria-label="end of column {ci + 1}"
						class="flex h-20 items-center justify-center rounded-xl border-2 border-dashed text-xs text-muted-foreground {dropAt === `end${ci}` ? 'border-primary text-primary' : ''}"
						ondragover={(e) => onOver(e, `end${ci}`)}
						ondrop={(e) => onDrop(e, ci, null)}
					>
						Drop here
					</div>
				{/if}
			</div>
		{/each}
	</div>
</div>

{#snippet panel(id: string)}
	{#if id === 'camera'}
		<DashPanel {id} {dash} title={cam?.name ?? 'Camera'} icon={icons.camera}>
			{#snippet actions()}
				<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={viewCamera} aria-label="camera">
					{#each cameras as c (c.id)}
						<ToggleGroup.Item value={c.id} class={segmentItem} title="{c.name} (looking {c.looking})">
							{c.id}<span class="text-[10px] opacity-60">{c.looking === 'up' ? '↑' : '↓'}</span>
						</ToggleGroup.Item>
					{/each}
				</ToggleGroup.Root>
			{/snippet}
			{#if viewCamera}
				{#key viewCamera}
					<CameraView id={viewCamera} headMounted={!!cam?.head} tool={(machine.selected_tool as string | null) ?? null} />
				{/key}
				{#if others.length}
					<div class="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
						{#each others as c (c.id)}
							<button class="group relative overflow-hidden rounded-md bg-black ring-1 ring-border hover:ring-primary" onclick={() => (viewCamera = c.id)} title="Show {c.name}">
								<StreamImg camera={c.id} mode="poll" fps={1} alt="camera {c.id}" class="aspect-[4/3] w-full object-cover opacity-80 group-hover:opacity-100" />
								<span class="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] text-white/90">{c.id} {c.looking === 'up' ? '↑' : '↓'}</span>
							</button>
						{/each}
					</div>
				{/if}
			{:else}
				<p class="text-sm text-muted-foreground">No cameras configured.</p>
			{/if}
		</DashPanel>
	{:else if id === 'toolhead'}
		<DashPanel {id} {dash} title="Toolhead" icon={icons.toolhead}>
			<JogPanel {tools} {distances} {liftDefault} />
		</DashPanel>
	{:else if id === 'actuators'}
		<DashPanel {id} {dash} title="Actuators" icon={icons.actuators} bodyClass="flex flex-col divide-y">
			{#each of('actuator') as a (a)}
				{@const s = st[a] ?? {}}
				{@const aid = idOf(a)}
				<div class="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
					<Label for="act-{aid}" class="flex flex-col items-start gap-0">
						<span class="text-sm font-medium">{aid}</span>
						<span class="text-xs font-normal text-muted-foreground">
							{s.value === null || s.value === undefined ? 'unknown' : s.value_type === 'boolean' ? (s.value ? 'on' : 'off') : fmt(s.value)}
						</span>
					</Label>
					<span class="ml-auto">
						{#if s.value_type === 'boolean'}
							<Switch
								id="act-{aid}"
								checked={s.value === true}
								disabled={!enabled}
								class={s.value === null || s.value === undefined ? 'data-[state=unchecked]:bg-warn/50' : ''}
								onCheckedChange={(v) => tinker.act('actuator.set', { id: aid, value: v })}
							/>
						{:else}
							<Input
								id="act-{aid}"
								class="h-8 w-24 text-right font-mono text-xs"
								value={s.value ?? ''}
								disabled={!enabled}
								onchange={(e) => {
									const v = (e.target as HTMLInputElement).value;
									tinker.act('actuator.set', { id: aid, value: s.value_type === 'double' ? Number(v) : v });
								}}
							/>
						{/if}
					</span>
				</div>
			{:else}
				<span class="text-sm text-muted-foreground">No actuators.</span>
			{/each}
		</DashPanel>
	{:else if id === 'macros'}
		<DashPanel {id} {dash} title="Macros" icon={icons.macros}>
			{#if macros.length}
				<div class="grid grid-cols-2 gap-2">
					{#each macros as m (m)}
						<Button variant="secondary" class="h-auto justify-start py-2 text-left whitespace-normal" disabled={!enabled || runningMacro !== null} onclick={() => runMacro(m)}>
							{#if runningMacro === m}<LoaderCircle class="animate-spin" />{:else}<Play class="text-primary" />{/if}
							{m}
						</Button>
					{/each}
				</div>
			{:else}
				<p class="text-xs leading-relaxed text-muted-foreground">
					No macros yet. Add them under <code class="font-mono">machine.macros</code> in the configuration: named lists of
					steps such as <code class="font-mono">move_to</code>, <code class="font-mono">actuate</code>,
					<code class="font-mono">dwell</code>, <code class="font-mono">gcode</code>.
				</p>
			{/if}
		</DashPanel>
	{:else if id === 'console'}
		<DashPanel {id} {dash} title="Console" icon={icons.console} bodyClass="p-0">
			<ConsoleView compact />
		</DashPanel>
	{:else if id === 'nozzles'}
		<DashPanel {id} {dash} title="Nozzles" icon={icons.nozzles}>
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Nozzle</Table.Head>
						<Table.Head class="text-right">X</Table.Head>
						<Table.Head class="text-right">Y</Table.Head>
						<Table.Head class="text-right">Z</Table.Head>
						<Table.Head class="text-right">R</Table.Head>
						<Table.Head>Tip</Table.Head>
						<Table.Head>Safe Z</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each of('nozzle') as n (n)}
						{@const s = st[n] ?? {}}
						{@const l = loc(s.location)}
						<Table.Row>
							<Table.Cell class="font-medium">{idOf(n)}</Table.Cell>
							<Table.Cell class="num text-right">{fmt(l.x)}</Table.Cell>
							<Table.Cell class="num text-right">{fmt(l.y)}</Table.Cell>
							<Table.Cell class="num text-right">{fmt(l.z)}</Table.Cell>
							<Table.Cell class="num text-right">{fmt(l.rotation)}</Table.Cell>
							<Table.Cell>{fmt(s.tip)}</Table.Cell>
							<Table.Cell>
								{#if s.in_safe_zone === true}<Badge variant="ok">safe</Badge>{:else if s.in_safe_zone === false}<Badge variant="warn">low</Badge>{:else}–{/if}
							</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		</DashPanel>
	{:else if id === 'axes'}
		<DashPanel {id} {dash} title="Axes" icon={icons.axes}>
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Axis</Table.Head>
						<Table.Head class="text-right">Position</Table.Head>
						<Table.Head>Homed</Table.Head>
						<Table.Head>Limits</Table.Head>
						<Table.Head>Driver / input</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each of('axis') as a (a)}
						{@const s = st[a] ?? {}}
						<Table.Row>
							<Table.Cell class="font-medium">{idOf(a)}{#if s.letter}<span class="ml-1 text-xs text-muted-foreground">{s.letter}</span>{/if}</Table.Cell>
							<Table.Cell class="num text-right">{fmt(s.position)}</Table.Cell>
							<Table.Cell>{#if s.homed}<Badge variant="ok">yes</Badge>{:else}<Badge variant="outline">no</Badge>{/if}</Table.Cell>
							<Table.Cell class="num text-xs text-muted-foreground">{s.soft_limits ? (s.soft_limits as number[]).join(' … ') : '–'}</Table.Cell>
							<Table.Cell class="text-xs text-muted-foreground">{fmt(s.driver ?? s.input ?? s.type)}</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		</DashPanel>
	{/if}
{/snippet}
