<script lang="ts">
	import { tinker, fmt, idOf, kindOf, type Loc } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Camera, ToggleLeft, ToggleRight } from '@lucide/svelte';

	const st = $derived(tinker.status);
	const names = $derived(tinker.objects);
	const of = (kind: string) => names.filter((n) => kindOf(n) === kind);

	const machine = $derived(st['machine'] ?? {});
	const motion = $derived(st['motion'] ?? {});

	function loc(v: unknown): Loc {
		return (v as Loc) ?? {};
	}
</script>

{#if tinker.connection !== 'connected' && !names.length}
	<p class="text-muted-foreground">Waiting for the TinkerPnP server…</p>
{:else}
	<div class="grid gap-4 lg:grid-cols-3">
		<Card.Card>
			<Card.CardHeader><Card.CardTitle>Machine</Card.CardTitle></Card.CardHeader>
			<Card.CardContent class="grid grid-cols-2 gap-y-1 text-sm">
				<span class="text-muted-foreground">State</span><span>{fmt(machine.state)}</span>
				<span class="text-muted-foreground">Enabled</span><span>{fmt(machine.enabled)}</span>
				<span class="text-muted-foreground">Homed</span><span>{fmt(machine.homed)}</span>
				<span class="text-muted-foreground">Alarm</span><span>{fmt(machine.alarm)}</span>
				<span class="text-muted-foreground">Selected tool</span><span>{fmt(machine.selected_tool)}</span>
				<span class="text-muted-foreground">All nozzles at safe Z</span>
				<span>
					{#if motion.safe_z === true}<Badge variant="secondary">yes</Badge>
					{:else if motion.safe_z === false}<Badge variant="destructive">no</Badge>
					{:else}–{/if}
				</span>
			</Card.CardContent>
		</Card.Card>

		<Card.Card class="lg:col-span-2">
			<Card.CardHeader><Card.CardTitle>Nozzles</Card.CardTitle></Card.CardHeader>
			<Card.CardContent>
				<Table.Table>
					<Table.TableHeader>
						<Table.TableRow>
							<Table.TableHead>Nozzle</Table.TableHead>
							<Table.TableHead class="text-right">X</Table.TableHead>
							<Table.TableHead class="text-right">Y</Table.TableHead>
							<Table.TableHead class="text-right">Z</Table.TableHead>
							<Table.TableHead class="text-right">Rotation</Table.TableHead>
							<Table.TableHead>Tip</Table.TableHead>
							<Table.TableHead>Part</Table.TableHead>
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
								<Table.TableCell>{fmt(s.part)}</Table.TableCell>
								<Table.TableCell>{fmt(s.in_safe_zone)}</Table.TableCell>
							</Table.TableRow>
						{/each}
					</Table.TableBody>
				</Table.Table>
			</Card.CardContent>
		</Card.Card>

		<Card.Card class="lg:col-span-2">
			<Card.CardHeader><Card.CardTitle>Axes</Card.CardTitle></Card.CardHeader>
			<Card.CardContent>
				<Table.Table>
					<Table.TableHeader>
						<Table.TableRow>
							<Table.TableHead>Axis</Table.TableHead>
							<Table.TableHead>Type</Table.TableHead>
							<Table.TableHead class="text-right">Position</Table.TableHead>
							<Table.TableHead>Homed</Table.TableHead>
							<Table.TableHead>Soft limits</Table.TableHead>
							<Table.TableHead>Safe zone</Table.TableHead>
							<Table.TableHead>Driver / input</Table.TableHead>
						</Table.TableRow>
					</Table.TableHeader>
					<Table.TableBody>
						{#each of('axis') as a (a)}
							{@const s = st[a] ?? {}}
							<Table.TableRow>
								<Table.TableCell class="font-medium">{idOf(a)}{s.letter ? ` (${s.letter})` : ''}</Table.TableCell>
								<Table.TableCell>{fmt(s.type)} · {fmt(s.kind)}</Table.TableCell>
								<Table.TableCell class="text-right tabular-nums">{fmt(s.position)}</Table.TableCell>
								<Table.TableCell>{fmt(s.homed)}</Table.TableCell>
								<Table.TableCell>{s.soft_limits ? (s.soft_limits as number[]).join(' … ') : '–'}</Table.TableCell>
								<Table.TableCell>{s.safe_zone ? (s.safe_zone as number[]).join(' … ') : '–'}</Table.TableCell>
								<Table.TableCell>{fmt(s.driver ?? s.input)}</Table.TableCell>
							</Table.TableRow>
						{/each}
					</Table.TableBody>
				</Table.Table>
			</Card.CardContent>
		</Card.Card>

		<Card.Card>
			<Card.CardHeader><Card.CardTitle>Actuators</Card.CardTitle></Card.CardHeader>
			<Card.CardContent class="flex flex-col gap-2 text-sm">
				{#each of('actuator') as a (a)}
					{@const s = st[a] ?? {}}
					<div class="flex items-center gap-2">
						{#if s.value === true}<ToggleRight class="size-5 text-green-600" />{:else}<ToggleLeft class="size-5 text-muted-foreground" />{/if}
						<span class="font-medium">{idOf(a)}</span>
						<span class="text-muted-foreground">{fmt(s.type)} · {fmt(s.value_type)}</span>
						<span class="ml-auto">{fmt(s.value)}</span>
					</div>
				{/each}
			</Card.CardContent>
		</Card.Card>

		<Card.Card class="lg:col-span-2">
			<Card.CardHeader><Card.CardTitle>Cameras</Card.CardTitle></Card.CardHeader>
			<Card.CardContent class="grid gap-4 sm:grid-cols-2">
				{#each of('camera') as c (c)}
					{@const s = st[c] ?? {}}
					{@const l = loc(s.location)}
					<div class="rounded-lg border p-3 text-sm">
						<div class="mb-2 flex items-center gap-2">
							<Camera class="size-4" />
							<span class="font-medium">{idOf(c)}</span>
							<Badge variant="outline">{fmt(s.looking)}</Badge>
							<Badge variant="outline">{fmt(s.type)}</Badge>
						</div>
						<div class="flex aspect-[4/3] items-center justify-center rounded-md bg-muted text-xs text-muted-foreground">
							video streams arrive in phase 1
						</div>
						<div class="mt-2 grid grid-cols-2 gap-y-0.5 text-xs">
							<span class="text-muted-foreground">Location</span>
							<span class="tabular-nums">{fmt(l.x)}, {fmt(l.y)}{l.z !== undefined ? `, ${fmt(l.z)}` : ''}</span>
							<span class="text-muted-foreground">Size</span><span>{fmt(s.width)} × {fmt(s.height)}</span>
							{#if s.last_error}<span class="text-muted-foreground">Status</span><span class="text-destructive">{fmt(s.last_error)}</span>{/if}
						</div>
					</div>
				{/each}
			</Card.CardContent>
		</Card.Card>

		<Card.Card>
			<Card.CardHeader><Card.CardTitle>Feeders</Card.CardTitle></Card.CardHeader>
			<Card.CardContent class="flex flex-col gap-2 text-sm">
				{#each of('feeder') as f (f)}
					{@const s = st[f] ?? {}}
					{@const l = loc(s.pick_location)}
					<div>
						<div class="flex items-center gap-2">
							<span class="font-medium">{idOf(f)}</span>
							<Badge variant="outline">{fmt(s.type)}</Badge>
							<span class="ml-auto">{fmt(s.part)}</span>
						</div>
						<div class="text-xs text-muted-foreground tabular-nums">pick at {fmt(l.x)}, {fmt(l.y)}, {fmt(l.z)}</div>
					</div>
				{:else}
					<span class="text-muted-foreground">No feeders configured.</span>
				{/each}
			</Card.CardContent>
		</Card.Card>
	</div>
{/if}
