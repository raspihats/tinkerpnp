<script lang="ts">
	import { tinker, fmt, idOf, kindOf, type Loc } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import CameraView from '#lib/components/machine/CameraView.svelte';
	import JogPanel from '#lib/components/machine/JogPanel.svelte';
	import { Power, PowerOff, House, SquareParking, ArrowUpToLine, ShieldAlert, LoaderCircle, Camera, Move, ToggleRight, Crosshair, Ruler } from '@lucide/svelte';

	type CameraInfo = { id: string; name: string; looking: string; head: string | null };

	const st = $derived(tinker.status);
	const names = $derived(tinker.objects);
	const of = (kind: string) => names.filter((n) => kindOf(n) === kind);

	const machine = $derived(st['machine'] ?? {});
	const enabled = $derived(machine.enabled === true);
	const homed = $derived(machine.homed === true);
	const inAlarm = $derived(machine.state === 'alarm');

	let cameras = $state<CameraInfo[]>([]);
	let viewCamera = $state<string>('');
	let distances = $state<number[]>([0.01, 0.1, 1, 10, 100]);
	let liftDefault = $state(true);

	// cameras and jog settings come from the server; reload when the configuration changes
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
			.call<{ config: { distances?: number[]; safe_z?: string } }>('config.get', { path: 'machine.jog' })
			.then((r) => {
				if (r.config?.distances?.length) distances = r.config.distances;
				liftDefault = r.config?.safe_z !== 'never';
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
</script>

<div class="flex flex-col gap-4">
	<!-- machine controls -->
	<div class="flex flex-wrap items-center gap-2">
		{#if !enabled}
			<Button size="lg" class="px-4" disabled={busy('machine.enable')} onclick={() => tinker.act('machine.enable')}>
				{#if busy('machine.enable')}<LoaderCircle class="animate-spin" />{:else}<Power />{/if} Enable
			</Button>
			<span class="text-sm text-muted-foreground">Enabling resets the controller and homes the machine.</span>
		{:else}
			<Button size="lg" variant="outline" disabled={busy('machine.home')} onclick={() => tinker.act('machine.home')}>
				{#if busy('machine.home')}<LoaderCircle class="animate-spin" />{:else}<House />{/if} Home
			</Button>
			<Button size="lg" variant="outline" disabled={!homed} onclick={() => tinker.act('motion.park')}><SquareParking /> Park</Button>
			<Button size="lg" variant="outline" disabled={!homed} onclick={() => tinker.act('motion.move_to_safe_z')}><ArrowUpToLine /> Safe Z</Button>
			{#if inAlarm}
				<Button size="lg" variant="outline" class="border-alarm/50 text-alarm" onclick={() => tinker.act('machine.clear_alarm')}>
					<ShieldAlert /> Clear alarm
				</Button>
			{/if}
			{#if st['motion']?.safe_z === false}
				<Badge variant="warn" class="ml-1">a nozzle is below safe Z</Badge>
			{/if}
			<Button size="lg" variant="ghost" class="ml-auto text-muted-foreground" onclick={() => tinker.act('machine.disable')}>
				<PowerOff /> Disable
			</Button>
		{/if}
	</div>

	<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]">
		<!-- camera -->
		<Card.Root class="gap-3">
			<Card.Header class="flex items-center gap-2">
				<Camera class="size-4 text-muted-foreground" />
				<Card.Title class="text-sm">{cam?.name ?? 'Camera'}</Card.Title>
				<Card.Action>
					<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={viewCamera} aria-label="camera">
						{#each cameras as c (c.id)}
							<ToggleGroup.Item value={c.id} class={segmentItem} title="{c.name} (looking {c.looking})">
								{c.id}<span class="text-[10px] opacity-60">{c.looking === 'up' ? '↑' : '↓'}</span>
							</ToggleGroup.Item>
						{/each}
					</ToggleGroup.Root>
				</Card.Action>
			</Card.Header>
			<Card.Content>
				{#if viewCamera}
					{#key viewCamera}
						<CameraView id={viewCamera} headMounted={!!cam?.head} tool={(machine.selected_tool as string | null) ?? null} />
					{/key}
				{:else}
					<p class="text-sm text-muted-foreground">No cameras configured.</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<div class="flex flex-col gap-4">
			<!-- jog -->
			<Card.Root class="gap-3">
				<Card.Header class="flex items-center gap-2">
					<Move class="size-4 text-muted-foreground" />
					<Card.Title class="text-sm">Jog</Card.Title>
				</Card.Header>
				<Card.Content>
					<JogPanel {tools} {distances} {liftDefault} />
				</Card.Content>
			</Card.Root>

			<!-- actuators -->
			<Card.Root class="gap-3">
				<Card.Header class="flex items-center gap-2">
					<ToggleRight class="size-4 text-muted-foreground" />
					<Card.Title class="text-sm">Actuators</Card.Title>
				</Card.Header>
				<Card.Content class="flex flex-col divide-y">
					{#each of('actuator') as a (a)}
						{@const s = st[a] ?? {}}
						{@const id = idOf(a)}
						<div class="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
							<Label for="act-{id}" class="flex flex-col items-start gap-0">
								<span class="text-sm font-medium">{id}</span>
								<span class="text-xs font-normal text-muted-foreground">
									{s.value === null || s.value === undefined ? 'unknown' : s.value_type === 'boolean' ? (s.value ? 'on' : 'off') : fmt(s.value)}
								</span>
							</Label>
							<span class="ml-auto">
								{#if s.value_type === 'boolean'}
									<Switch
										id="act-{id}"
										checked={s.value === true}
										disabled={!enabled}
										class={s.value === null || s.value === undefined ? 'data-[state=unchecked]:bg-warn/50' : ''}
										onCheckedChange={(v) => tinker.act('actuator.set', { id, value: v })}
									/>
								{:else}
									<Input
										id="act-{id}"
										class="h-8 w-24 text-right font-mono text-xs"
										value={s.value ?? ''}
										disabled={!enabled}
										onchange={(e) => {
											const v = (e.target as HTMLInputElement).value;
											tinker.act('actuator.set', { id, value: s.value_type === 'double' ? Number(v) : v });
										}}
									/>
								{/if}
							</span>
						</div>
					{:else}
						<span class="text-sm text-muted-foreground">No actuators.</span>
					{/each}
				</Card.Content>
			</Card.Root>
		</div>
	</div>

	<div class="grid items-start gap-4 lg:grid-cols-2">
		<Card.Root class="gap-2">
			<Card.Header class="flex items-center gap-2">
				<Crosshair class="size-4 text-muted-foreground" />
				<Card.Title class="text-sm">Nozzles</Card.Title>
			</Card.Header>
			<Card.Content>
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
			</Card.Content>
		</Card.Root>

		<Card.Root class="gap-2">
			<Card.Header class="flex items-center gap-2">
				<Ruler class="size-4 text-muted-foreground" />
				<Card.Title class="text-sm">Axes</Card.Title>
			</Card.Header>
			<Card.Content>
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
			</Card.Content>
		</Card.Root>
	</div>
</div>
