<script lang="ts">
	import { tinker, fmt, idOf, kindOf, type Loc } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import CameraView from '#lib/components/machine/CameraView.svelte';
	import JogPanel from '#lib/components/machine/JogPanel.svelte';
	import { Power, PowerOff, House, ParkingSquare, ArrowUpToLine, ShieldAlert, LoaderCircle } from '@lucide/svelte';

	type CameraInfo = { id: string; name: string; looking: string; head: string | null };

	const st = $derived(tinker.status);
	const names = $derived(tinker.objects);
	const of = (kind: string) => names.filter((n) => kindOf(n) === kind);

	const machine = $derived(st['machine'] ?? {});
	const motion = $derived(st['motion'] ?? {});
	const enabled = $derived(machine.enabled === true);
	const homed = $derived(machine.homed === true);
	const inAlarm = $derived(machine.state === 'alarm');

	let cameras = $state<CameraInfo[]>([]);
	let viewCamera = $state<string | null>(null);
	let distances = $state<number[]>([0.01, 0.1, 1, 10, 100]);
	let liftDefault = $state(true);

	// cameras and jog settings come from the server; reload when the configuration changes
	$effect(() => {
		void tinker.configRevision;
		if (tinker.connection !== 'connected') return;
		tinker.call<CameraInfo[]>('camera.list').then((list) => {
			cameras = list;
			if (!viewCamera || !list.some((c) => c.id === viewCamera)) {
				viewCamera = (list.find((c) => c.head && c.looking === 'down') ?? list[0])?.id ?? null;
			}
		}).catch(() => {});
		tinker.call<{ config: { distances?: number[]; safe_z?: string } }>('config.get', { path: 'machine.jog' })
			.then((r) => {
				if (r.config?.distances?.length) distances = r.config.distances;
				liftDefault = r.config?.safe_z !== 'never';
			})
			.catch(() => {});
	});

	// jog targets: nozzles and head-mounted cameras
	const tools = $derived([...of('nozzle'), ...cameras.filter((c) => c.head).map((c) => `camera/${c.id}`)]);

	// following the selected tool: a selected head camera is also shown
	$effect(() => {
		const sel = machine.selected_tool as string | undefined;
		if (sel?.startsWith('camera/')) viewCamera = idOf(sel);
	});

	const busy = (...m: string[]) => tinker.isBusy(...m);

	function loc(v: unknown): Loc {
		return (v as Loc) ?? {};
	}

	function setActuator(id: string, value: unknown) {
		tinker.act('actuator.set', { id, value });
	}
</script>

{#if tinker.connection !== 'connected' && !names.length}
	<p class="text-muted-foreground">Waiting for the TinkerPnP server…</p>
{:else}
	<div class="flex flex-col gap-4">
		<!-- machine bar -->
		<Card.Card class="py-3">
			<Card.CardContent class="flex flex-wrap items-center gap-2 px-3 text-sm">
				<Badge variant={inAlarm ? 'destructive' : enabled ? 'default' : 'outline'} class="text-sm">{fmt(machine.state)}</Badge>
				<Badge variant={homed ? 'secondary' : 'outline'}>{homed ? 'homed' : 'not homed'}</Badge>
				{#if motion.safe_z === false}<Badge variant="outline" class="border-amber-500 text-amber-700 dark:text-amber-400">nozzle below safe Z</Badge>{/if}
				{#if machine.message}<span class="text-xs text-muted-foreground">{machine.message}</span>{/if}
				<span class="ml-auto flex flex-wrap gap-1">
					{#if !enabled}
						<Button size="sm" disabled={busy('machine.enable')} onclick={() => tinker.act('machine.enable')}>
							{#if busy('machine.enable')}<LoaderCircle class="size-4 animate-spin" />{:else}<Power class="size-4" />{/if} Enable
						</Button>
					{:else}
						<Button size="sm" variant="outline" disabled={busy('machine.home')} onclick={() => tinker.act('machine.home')}>
							{#if busy('machine.home')}<LoaderCircle class="size-4 animate-spin" />{:else}<House class="size-4" />{/if} Home
						</Button>
						<Button size="sm" variant="outline" disabled={!homed} onclick={() => tinker.act('motion.park')}>
							<ParkingSquare class="size-4" /> Park
						</Button>
						<Button size="sm" variant="outline" disabled={!homed} onclick={() => tinker.act('motion.move_to_safe_z')}>
							<ArrowUpToLine class="size-4" /> Safe Z
						</Button>
						{#if inAlarm}
							<Button size="sm" variant="outline" onclick={() => tinker.act('machine.clear_alarm')}>
								<ShieldAlert class="size-4" /> Clear alarm
							</Button>
						{/if}
						<Button size="sm" variant="secondary" onclick={() => tinker.act('machine.disable')}>
							<PowerOff class="size-4" /> Disable
						</Button>
					{/if}
				</span>
			</Card.CardContent>
		</Card.Card>

		<div class="grid gap-4 lg:grid-cols-5">
			<!-- camera -->
			<Card.Card class="gap-2 py-3 lg:col-span-3">
				<Card.CardHeader class="flex flex-row items-center gap-1 px-3">
					<Card.CardTitle class="mr-2 text-sm">Camera</Card.CardTitle>
					{#each cameras as c (c.id)}
						<button onclick={() => (viewCamera = c.id)}>
							<Badge variant={viewCamera === c.id ? 'default' : 'outline'}>{c.id} · {c.looking}</Badge>
						</button>
					{/each}
				</Card.CardHeader>
				<Card.CardContent class="px-3">
					{#if viewCamera}
						{#key viewCamera}
							<CameraView id={viewCamera} headMounted={!!cameras.find((c) => c.id === viewCamera)?.head} tool={(machine.selected_tool as string | null) ?? null} />
						{/key}
					{:else}
						<p class="text-sm text-muted-foreground">No cameras configured.</p>
					{/if}
				</Card.CardContent>
			</Card.Card>

			<div class="flex flex-col gap-4 lg:col-span-2">
				<!-- jog -->
				<Card.Card class="gap-2 py-3">
					<Card.CardHeader class="px-3"><Card.CardTitle class="text-sm">Jog</Card.CardTitle></Card.CardHeader>
					<Card.CardContent class="px-3">
						<JogPanel {tools} {distances} {liftDefault} />
					</Card.CardContent>
				</Card.Card>

				<!-- actuators -->
				<Card.Card class="gap-2 py-3">
					<Card.CardHeader class="px-3"><Card.CardTitle class="text-sm">Actuators</Card.CardTitle></Card.CardHeader>
					<Card.CardContent class="flex flex-col gap-1.5 px-3 text-sm">
						{#each of('actuator') as a (a)}
							{@const s = st[a] ?? {}}
							<div class="flex items-center gap-2">
								<span class="font-medium">{idOf(a)}</span>
								<span class="text-xs text-muted-foreground">{fmt(s.type)}</span>
								<span class="ml-auto">
									{#if s.value_type === 'boolean'}
										<button
											role="switch"
											aria-checked={s.value === true}
											aria-label="switch {idOf(a)}"
											disabled={!enabled}
											onclick={() => setActuator(idOf(a), s.value !== true)}
											title={s.value === null || s.value === undefined ? 'unknown: not set since the last enable, stop or alarm' : ''}
											class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:opacity-40 {s.value === true ? 'bg-green-600' : s.value === false ? 'bg-muted-foreground/30' : 'bg-amber-400/60'}"
										>
											<span class="inline-block size-4 rounded-full bg-white shadow transition-transform {s.value === true ? 'translate-x-4.5' : s.value === false ? 'translate-x-0.5' : 'translate-x-2.5'}"></span>
										</button>
									{:else}
										<input
											class="w-20 rounded border bg-background px-1 text-right font-mono text-xs"
											value={s.value ?? ''}
											disabled={!enabled}
											onchange={(e) => {
												const v = (e.target as HTMLInputElement).value;
												setActuator(idOf(a), s.value_type === 'double' ? Number(v) : v);
											}}
										/>
									{/if}
								</span>
							</div>
						{:else}
							<span class="text-muted-foreground">No actuators.</span>
						{/each}
					</Card.CardContent>
				</Card.Card>
			</div>
		</div>

		<div class="grid gap-4 lg:grid-cols-2">
			<Card.Card class="gap-2 py-3">
				<Card.CardHeader class="px-3"><Card.CardTitle class="text-sm">Nozzles</Card.CardTitle></Card.CardHeader>
				<Card.CardContent class="px-3">
					<Table.Table>
						<Table.TableHeader>
							<Table.TableRow>
								<Table.TableHead>Nozzle</Table.TableHead>
								<Table.TableHead class="text-right">X</Table.TableHead>
								<Table.TableHead class="text-right">Y</Table.TableHead>
								<Table.TableHead class="text-right">Z</Table.TableHead>
								<Table.TableHead class="text-right">R</Table.TableHead>
								<Table.TableHead>Tip</Table.TableHead>
								<Table.TableHead>Safe Z</Table.TableHead>
							</Table.TableRow>
						</Table.TableHeader>
						<Table.TableBody>
							{#each of('nozzle') as n (n)}
								{@const s = st[n] ?? {}}
								{@const l = loc(s.location)}
								<Table.TableRow>
									<Table.TableCell class="font-medium">{idOf(n)}</Table.TableCell>
									<Table.TableCell class="text-right tabular-nums">{fmt(l.x)}</Table.TableCell>
									<Table.TableCell class="text-right tabular-nums">{fmt(l.y)}</Table.TableCell>
									<Table.TableCell class="text-right tabular-nums">{fmt(l.z)}</Table.TableCell>
									<Table.TableCell class="text-right tabular-nums">{fmt(l.rotation)}</Table.TableCell>
									<Table.TableCell>{fmt(s.tip)}</Table.TableCell>
									<Table.TableCell>{fmt(s.in_safe_zone)}</Table.TableCell>
								</Table.TableRow>
							{/each}
						</Table.TableBody>
					</Table.Table>
				</Card.CardContent>
			</Card.Card>

			<Card.Card class="gap-2 py-3">
				<Card.CardHeader class="px-3"><Card.CardTitle class="text-sm">Axes</Card.CardTitle></Card.CardHeader>
				<Card.CardContent class="px-3">
					<Table.Table>
						<Table.TableHeader>
							<Table.TableRow>
								<Table.TableHead>Axis</Table.TableHead>
								<Table.TableHead class="text-right">Position</Table.TableHead>
								<Table.TableHead>Homed</Table.TableHead>
								<Table.TableHead>Limits</Table.TableHead>
								<Table.TableHead>Driver / input</Table.TableHead>
							</Table.TableRow>
						</Table.TableHeader>
						<Table.TableBody>
							{#each of('axis') as a (a)}
								{@const s = st[a] ?? {}}
								<Table.TableRow>
									<Table.TableCell class="font-medium">{idOf(a)}{s.letter ? ` (${s.letter})` : ''}</Table.TableCell>
									<Table.TableCell class="text-right tabular-nums">{fmt(s.position)}</Table.TableCell>
									<Table.TableCell>{fmt(s.homed)}</Table.TableCell>
									<Table.TableCell class="text-xs">{s.soft_limits ? (s.soft_limits as number[]).join(' … ') : '–'}</Table.TableCell>
									<Table.TableCell class="text-xs">{fmt(s.driver ?? s.input ?? s.type)}</Table.TableCell>
								</Table.TableRow>
							{/each}
						</Table.TableBody>
					</Table.Table>
				</Card.CardContent>
			</Card.Card>
		</div>
	</div>
{/if}
