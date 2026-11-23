<script lang="ts">
	// Jogging and absolute moves for the selected tool (machine.selected_tool, shared by all clients).
	// Keys (when no input has focus): arrows = X/Y, Page Up/Down = Z, comma/period = rotation, Shift = 10x step.
	import { tinker, fmt, idOf } from '#lib/api/client.svelte.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { ArrowUp, ArrowDown, ArrowLeft, ArrowRight, ChevronsUp, ChevronsDown, RotateCcw, RotateCw, LoaderCircle } from '@lucide/svelte';

	let { tools, distances, liftDefault = true }: { tools: string[]; distances: number[]; liftDefault?: boolean } = $props();

	const ROTATIONS = [0.1, 1, 5, 15, 45, 90];
	const SPEEDS = [0.1, 0.25, 0.5, 1];

	let step = $state(1);
	let rotStep = $state(5);
	let speed = $state(0.5);
	// raise all nozzles to safe Z before X/Y jogs; starts from machine.jog.safe_z and is not remembered on purpose
	let lift = $state(true);
	$effect(() => {
		lift = liftDefault;
	});
	let target = $state<{ x: string; y: string; z: string; rotation: string }>({ x: '', y: '', z: '', rotation: '' });

	$effect(() => {
		if (!distances.includes(step)) step = distances.includes(1) ? 1 : distances[Math.floor(distances.length / 2)];
	});

	const machine = $derived(tinker.status['machine'] ?? {});
	const tool = $derived((machine.selected_tool as string | null) ?? tools[0] ?? null);
	const ready = $derived(machine.enabled === true && machine.homed === true && machine.state !== 'alarm');
	const loc = $derived(((tool && tinker.status[tool]?.location) ?? {}) as { x?: number; y?: number; z?: number; rotation?: number });
	const moving = $derived(tinker.isBusy('motion.jog', 'motion.move_to'));

	function jog(delta: Record<string, number>) {
		if (!tool || !ready || moving) return;        // one jog at a time: holding a key doesn't queue up moves
		tinker.act('motion.jog', { object: tool, delta, speed, safe_z: lift ? 'auto' : 'none' });
	}

	function moveTo() {
		if (!tool) return;
		const location: Record<string, number> = {};
		for (const k of ['x', 'y', 'z', 'rotation'] as const) {
			const v = target[k].trim();
			if (v !== '') location[k] = Number(v);
		}
		if (Object.keys(location).length) tinker.act('motion.move_to', { object: tool, location, speed });
	}

	function onKey(e: KeyboardEvent) {
		const t = e.target as HTMLElement;
		if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
		const k = e.shiftKey ? 10 : 1;
		const map: Record<string, Record<string, number>> = {
			ArrowLeft: { x: -step * k },
			ArrowRight: { x: step * k },
			ArrowUp: { y: step * k },
			ArrowDown: { y: -step * k },
			PageUp: { z: step * k },
			PageDown: { z: -step * k },
			',': { rotation: rotStep },
			'.': { rotation: -rotStep }
		};
		const delta = map[e.key];
		if (delta) {
			e.preventDefault();
			jog(delta);
		}
	}
</script>

<svelte:window onkeydown={onKey} />

<div class="flex flex-col gap-3 text-sm">
	<div class="flex flex-wrap items-center gap-1">
		<span class="mr-1 text-xs text-muted-foreground">Tool</span>
		{#each tools as t (t)}
			<button onclick={() => tinker.act('machine.select_tool', { object: t })}>
				<Badge variant={t === tool ? 'default' : 'outline'}>{idOf(t)}</Badge>
			</button>
		{/each}
	</div>

	<div class="grid grid-cols-4 gap-1 rounded-md bg-muted/60 p-2 text-center font-mono tabular-nums">
		{#each [['X', loc.x], ['Y', loc.y], ['Z', loc.z], ['R', loc.rotation]] as [label, v] (label)}
			<div>
				<div class="text-[10px] text-muted-foreground">{label}</div>
				<div class="text-base font-semibold">{fmt(v as number | undefined, 3)}</div>
			</div>
		{/each}
	</div>

	<div class="flex items-start gap-4">
		<div class="grid grid-cols-3 gap-1">
			<span></span>
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ y: step })} title="Y+ (↑)"><ArrowUp /></Button>
			<span></span>
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ x: -step })} title="X− (←)"><ArrowLeft /></Button>
			<span class="flex items-center justify-center">{#if moving}<LoaderCircle class="size-4 animate-spin text-muted-foreground" />{/if}</span>
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ x: step })} title="X+ (→)"><ArrowRight /></Button>
			<span></span>
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ y: -step })} title="Y− (↓)"><ArrowDown /></Button>
			<span></span>
		</div>
		<div class="grid gap-1">
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ z: step })} title="Z+ (Page Up)"><ChevronsUp /></Button>
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ z: -step })} title="Z− (Page Down)"><ChevronsDown /></Button>
		</div>
		<div class="grid gap-1">
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ rotation: rotStep })} title="Rotate + (,)"><RotateCcw /></Button>
			<Button variant="outline" size="icon" disabled={!ready} onclick={() => jog({ rotation: -rotStep })} title="Rotate − (.)"><RotateCw /></Button>
		</div>
		<div class="flex flex-col gap-1 text-xs">
			<label class="flex items-center justify-between gap-2">Step
				<select class="rounded border bg-background px-1 py-0.5" bind:value={step}>
					{#each distances as d (d)}<option value={d}>{d} mm</option>{/each}
				</select>
			</label>
			<label class="flex items-center justify-between gap-2">Rotate
				<select class="rounded border bg-background px-1 py-0.5" bind:value={rotStep}>
					{#each ROTATIONS as d (d)}<option value={d}>{d}°</option>{/each}
				</select>
			</label>
			<label class="flex items-center justify-between gap-2">Speed
				<select class="rounded border bg-background px-1 py-0.5" bind:value={speed}>
					{#each SPEEDS as v (v)}<option value={v}>{v * 100} %</option>{/each}
				</select>
			</label>
		</div>
	</div>

	<label class="flex items-center gap-2 text-xs">
		<input type="checkbox" bind:checked={lift} />
		Raise Z before X/Y jogs
		{#if !lift}
			<span class="rounded bg-amber-100 px-1.5 py-0.5 text-amber-900 dark:bg-amber-950 dark:text-amber-200">
				off: X/Y jogs stay at this Z — watch for anything under the nozzles
			</span>
		{/if}
	</label>

	<form class="flex items-center gap-1" onsubmit={(e) => { e.preventDefault(); moveTo(); }}>
		{#each ['x', 'y', 'z', 'rotation'] as const as k (k)}
			<input
				class="w-full min-w-0 rounded border bg-background px-1.5 py-1 font-mono text-xs"
				placeholder={k === 'rotation' ? 'R' : k.toUpperCase()}
				inputmode="decimal"
				bind:value={target[k]}
			/>
		{/each}
		<Button type="submit" size="sm" disabled={!ready || moving}>Go</Button>
	</form>
	<p class="text-xs text-muted-foreground">
		Arrows, Page Up/Down and , . jog (Shift: ×10). Empty fields keep their value. Go, Park and click-to-move always
		raise the nozzles to safe Z before X/Y travel.
	</p>
</div>
