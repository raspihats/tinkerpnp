<script lang="ts">
	// Jogging and absolute moves for the selected tool (machine.selected_tool, shared by all clients).
	// Keys (when no input has focus): arrows = X/Y, Page Up/Down = Z, comma/period = rotation, Shift = 10x step.
	import { tinker, idOf, RpcError } from '#lib/api/client.svelte.js';
	import { toast } from 'svelte-sonner';
	import { segmentItem } from '#lib/styles.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import Dro from './Dro.svelte';
	import {
		ArrowUp,
		ArrowDown,
		ArrowLeft,
		ArrowRight,
		ChevronsUp,
		ChevronsDown,
		RotateCcw,
		RotateCw,
		LoaderCircle,
		Move,
		TriangleAlert,
		House,
		SquareParking,
		ArrowUpToLine
	} from '@lucide/svelte';

	let { tools, distances }: { tools: string[]; distances: number[] } = $props();

	const ROTATIONS = [0.1, 1, 5, 15, 45, 90];
	const SPEEDS = [0.1, 0.25, 0.5, 1];

	let step = $state('1');
	let rotStep = $state('5');
	let speed = $state('0.5');
	// As OpenPnP: jogs move straight there at the current Z (machine.jog.safe_z), and board protection refuses jogs
	// that would bring a nozzle below safe Z over a board of the loaded job. On at every start, not remembered.
	let boardProtection = $state(true);
	let target = $state<{ x: string; y: string; z: string; rotation: string }>({ x: '', y: '', z: '', rotation: '' });

	$effect(() => {
		const d = distances.map(String);
		if (!d.includes(step)) step = d.includes('1') ? '1' : d[Math.floor(d.length / 2)];
	});

	const machine = $derived(tinker.status['machine'] ?? {});
	const tool = $derived((machine.selected_tool as string | null) ?? tools[0] ?? null);
	const ready = $derived(machine.enabled === true && machine.homed === true && machine.state !== 'alarm');
	const loc = $derived(((tool && tinker.status[tool]?.location) ?? {}) as { x?: number; y?: number; z?: number; rotation?: number });
	const moving = $derived(tinker.isBusy('motion.jog', 'motion.move_to'));
	const lastMove = $derived(tinker.status['motion']?.last_move as { object?: string; requested?: Record<string, number> } | undefined);
	const requested = $derived(lastMove?.object === tool ? (lastMove?.requested ?? null) : null);
	const enabled = $derived(machine.enabled === true);

	function jog(delta: Record<string, number>, minStep = false) {
		if (!tool || !ready || moving) return; // one jog at a time: holding a key doesn't queue up moves
		const params = { object: tool, delta, speed: Number(speed), min_step: minStep, board_protection: boardProtection };
		tinker.act('motion.jog', params, 120000, (e: RpcError) => {
			if (e.data?.name !== 'below_resolution') return false;
			// the axis can't move that little (e.g. Z in 0.225 mm motor steps): offer the minimum step instead
			const min = Number(e.data.min_step);
			const coord = String(e.data.coord ?? '');
			const unit = coord === 'rotation' ? '°' : ' mm';
			toast.warning(`${coord.toUpperCase()} can't move ${Math.abs(Number(e.data.delta))}${unit}`, {
				description: `The minimum step of ${coord.toUpperCase()} is ${min.toFixed(3)}${unit}. Move by the minimum step instead?`,
				duration: 10000,
				action: { label: `Move ${min.toFixed(3)}${unit}`, onClick: () => jog(delta, true) }
			});
			return true;
		});
	}
	const s = () => Number(step);
	const r = () => Number(rotStep);

	function moveTo(e: SubmitEvent) {
		e.preventDefault();
		if (!tool) return;
		const location: Record<string, number> = {};
		for (const k of ['x', 'y', 'z', 'rotation'] as const) {
			const v = target[k].trim();
			if (v !== '') location[k] = Number(v);
		}
		if (Object.keys(location).length) tinker.act('motion.move_to', { object: tool, location, speed: Number(speed) });
	}

	function onKey(e: KeyboardEvent) {
		const t = e.target as HTMLElement;
		if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
		const k = e.shiftKey ? 10 : 1;
		const map: Record<string, Record<string, number>> = {
			ArrowLeft: { x: -s() * k },
			ArrowRight: { x: s() * k },
			ArrowUp: { y: s() * k },
			ArrowDown: { y: -s() * k },
			PageUp: { z: s() * k },
			PageDown: { z: -s() * k },
			',': { rotation: r() },
			'.': { rotation: -r() }
		};
		const delta = map[e.key];
		if (delta) {
			e.preventDefault();
			jog(delta);
		}
	}

	const pad = 'size-12 rounded-lg';
</script>

<svelte:window onkeydown={onKey} />

<div class="flex flex-col gap-4">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<span class="eyebrow">Tool</span>
		<ToggleGroup.Root
			type="single"
			variant="outline"
			size="sm"
			value={tool ?? ''}
			onValueChange={(v) => v && tinker.act('machine.select_tool', { object: v })}
		>
			{#each tools as t (t)}
				<ToggleGroup.Item value={t} class={segmentItem}>{idOf(t)}</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
	</div>

	<Dro location={loc} {requested} />

	<div class="flex items-center justify-center gap-6">
		<!-- X/Y pad -->
		<div class="grid grid-cols-3 gap-1.5">
			<span></span>
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ y: s() })} title="Y+ (↑)"><ArrowUp class="size-5" /></Button>
			<span></span>
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ x: -s() })} title="X− (←)"><ArrowLeft class="size-5" /></Button>
			<div class="flex size-12 items-center justify-center rounded-lg border border-dashed text-muted-foreground">
				{#if moving}<LoaderCircle class="size-5 animate-spin text-busy" />{:else}<Move class="size-4" />{/if}
			</div>
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ x: s() })} title="X+ (→)"><ArrowRight class="size-5" /></Button>
			<span></span>
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ y: -s() })} title="Y− (↓)"><ArrowDown class="size-5" /></Button>
			<span></span>
		</div>
		<!-- Z -->
		<div class="flex flex-col items-center gap-1.5">
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ z: s() })} title="Z+ (Page Up)"><ChevronsUp class="size-5" /></Button>
			<span class="text-xs font-bold text-axis-z">Z</span>
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ z: -s() })} title="Z− (Page Down)"><ChevronsDown class="size-5" /></Button>
		</div>
		<!-- rotation -->
		<div class="flex flex-col items-center gap-1.5">
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ rotation: r() })} title="Rotate + (,)"><RotateCcw class="size-5" /></Button>
			<span class="text-xs font-bold text-axis-r">R</span>
			<Button variant="secondary" class={pad} disabled={!ready} onclick={() => jog({ rotation: -r() })} title="Rotate − (.)"><RotateCw class="size-5" /></Button>
		</div>
	</div>

	<div class="grid grid-cols-3 gap-1.5">
		<Button variant="outline" size="sm" disabled={!enabled || tinker.isBusy('machine.home')} onclick={() => tinker.act('machine.home')}>
			{#if tinker.isBusy('machine.home')}<LoaderCircle class="animate-spin" />{:else}<House />{/if} Home
		</Button>
		<Button variant="outline" size="sm" disabled={!ready} onclick={() => tinker.act('motion.park')}><SquareParking /> Park</Button>
		<Button variant="outline" size="sm" disabled={!ready} onclick={() => tinker.act('motion.move_to_safe_z')}><ArrowUpToLine /> Safe Z</Button>
	</div>

	<div class="grid grid-cols-[4.5rem_1fr] items-center gap-x-3 gap-y-2">
		<span class="eyebrow">Step</span>
		<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={step} class="w-full">
			{#each distances as d (d)}
				<ToggleGroup.Item value={String(d)} class="flex-1 font-mono {segmentItem}">{d}</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
		<span class="eyebrow">Rotate °</span>
		<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={rotStep} class="w-full">
			{#each ROTATIONS as d (d)}
				<ToggleGroup.Item value={String(d)} class="flex-1 font-mono {segmentItem}">{d}</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
		<span class="eyebrow">Speed</span>
		<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={speed} class="w-full">
			{#each SPEEDS as v (v)}
				<ToggleGroup.Item value={String(v)} class="flex-1 font-mono {segmentItem}">{v * 100}%</ToggleGroup.Item>
			{/each}
		</ToggleGroup.Root>
	</div>

	<div class="flex flex-col gap-1.5">
		<div class="flex items-center gap-2.5">
			<Switch id="board-protection" bind:checked={boardProtection} />
			<Label for="board-protection" class="text-sm font-normal" title="Refuse jogs that would bring a nozzle below safe Z over a board of the loaded job (as OpenPnP)">
				Board protection
			</Label>
		</div>
		{#if !boardProtection}
			<p class="flex items-center gap-1.5 rounded-md bg-warn/12 px-2.5 py-1.5 text-xs text-warn">
				<TriangleAlert class="size-3.5 shrink-0" /> Jogs may lower a nozzle onto a board. Back on at the next start
			</p>
		{/if}
	</div>

	<form class="flex items-center gap-1.5" onsubmit={moveTo}>
		{#each [['x', 'X'], ['y', 'Y'], ['z', 'Z'], ['rotation', 'R']] as const as [k, label] (k)}
			<Input class="h-8 min-w-0 font-mono text-xs" placeholder={label} inputmode="decimal" aria-label="target {label}" bind:value={target[k]} />
		{/each}
		<Button type="submit" size="sm" class="h-8" disabled={!ready || moving}>Go</Button>
	</form>

	<p class="text-xs leading-relaxed text-muted-foreground">
		<kbd class="kbd">←→↑↓</kbd> X/Y · <kbd class="kbd">PgUp</kbd><kbd class="kbd">PgDn</kbd> Z ·
		<kbd class="kbd">,</kbd><kbd class="kbd">.</kbd> rotate · <kbd class="kbd">Shift</kbd> ×10. Jogs stay at the current Z (as OpenPnP); Go,
		Park, Home and click-to-move raise to safe Z before X/Y travel.
	</p>
</div>
