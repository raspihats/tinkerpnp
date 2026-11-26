<script lang="ts">
	// Jogging and absolute moves for the selected tool (machine.selected_tool, shared by all clients).
	// Keys (when no input has focus): arrows = X/Y, Page Up/Down = Z, comma/period = rotation, Shift = finer (two step sizes smaller, as OpenPnP).
	import { tinker, idOf, RpcError } from '#lib/api/client.svelte.js';
	import { toast } from 'svelte-sonner';
	import { segmentItem } from '#lib/styles.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
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

	let {
		tools,
		distances,
		lift = { safe_z: 'never', lift_above: 5 }
	}: { tools: string[]; distances: number[]; lift?: { safe_z: string; lift_above: number } } = $props();
	const liftText = $derived(
		lift.safe_z === 'always'
			? 'X/Y jogs raise to safe Z first'
			: lift.safe_z === 'long'
				? `X/Y jogs over ${lift.lift_above} mm raise to safe Z first, shorter ones stay at the current Z`
				: 'Jogs stay at the current Z (as OpenPnP)'
	);

	const ROTATIONS = [0.1, 1, 5, 15, 45, 90];

	let step = $state('1');
	let rotStep = $state('5');
	// As OpenPnP: jogs move straight there at the current Z (machine.jog.safe_z), and board protection refuses jogs
	// that would bring a nozzle below safe Z over a board of the loaded job. On at every start, not remembered.
	let boardProtection = $state(true);

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
		const params = { object: tool, delta, min_step: minStep, board_protection: boardProtection };
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
	const globalSpeed = $derived(Number(tinker.status['motion']?.speed ?? 1));
	let speedDraft = $state<number | null>(null);
	async function setSpeed(pct: number) {
		await tinker.act('machine.set_speed', { speed: pct / 100 });
		speedDraft = null;
	}
	const s = () => Number(step);
	/** As OpenPnP: Shift jogs two step sizes finer (10 -> 0.1), never coarser; at most down to the smallest step. */
	const fine = () => {
		const sorted = [...distances].sort((a, b) => a - b);
		const i = sorted.indexOf(s());
		return i < 0 ? s() / 100 : sorted[Math.max(0, i - 2)];
	};
	const r = () => Number(rotStep);

	function onKey(e: KeyboardEvent) {
		const t = e.target as HTMLElement;
		if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
		const d = e.shiftKey ? fine() : s();
		const map: Record<string, Record<string, number>> = {
			ArrowLeft: { x: -d },
			ArrowRight: { x: d },
			ArrowUp: { y: d },
			ArrowDown: { y: -d },
			PageUp: { z: d },
			PageDown: { z: -d },
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

	<Dro
		location={loc}
		{requested}
		onset={ready && tool
			? (key, value) => tinker.act('motion.move_to', { object: tool, location: { [key]: value } })
			: null}
	/>

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
		<Button variant="outline" size="sm" disabled={!ready} onclick={() => tinker.act('motion.park')} title="Safe Z, then the park location (OpenPnP's X/Y park)"><SquareParking /> Park</Button>
		<Button variant="outline" size="sm" disabled={!ready} onclick={() => tinker.act('motion.park', { axes: 'z' })} title="All nozzles to safe Z (OpenPnP's Z park)">Z park</Button>
		<Button variant="outline" size="sm" disabled={!ready} onclick={() => tinker.act('motion.park', { axes: 'rotation' })} title="Nozzle rotations to 0° or the middle of their range (OpenPnP's C park)">C park</Button>
		<Button variant="outline" size="sm" disabled={!ready} onclick={() => tinker.act('motion.move_to_safe_z')}><ArrowUpToLine /> Safe Z</Button>
	</div>

	<div class="grid grid-cols-[4.5rem_1fr] items-center gap-x-3 gap-y-2">
		<span class="eyebrow">Step <span class="normal-case">mm</span></span>
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
		<!-- as OpenPnP's speed slider: global, for every move incl. jobs -->
		<div class="flex items-center gap-2" title="Global speed for every move (jogs, Go, jobs), as OpenPnP's slider">
			<input
				type="range"
				min="1"
				max="100"
				step="1"
				class="w-full accent-primary"
				value={Math.round(globalSpeed * 100)}
				oninput={(e) => (speedDraft = Number(e.currentTarget.value))}
				onchange={(e) => setSpeed(Number(e.currentTarget.value))}
			/>
			<span class="w-10 text-right font-mono text-xs">{speedDraft ?? Math.round(globalSpeed * 100)}%</span>
		</div>
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


	<p class="text-xs leading-relaxed text-muted-foreground">
		<kbd class="kbd">←→↑↓</kbd> X/Y · <kbd class="kbd">PgUp</kbd><kbd class="kbd">PgDn</kbd> Z ·
		<kbd class="kbd">,</kbd><kbd class="kbd">.</kbd> rotate · <kbd class="kbd">Shift</kbd> finer (two steps smaller). {liftText}. Click a position above to type one (Enter moves); typed positions,
		Park, Home and click-to-move raise to safe Z before X/Y travel.
	</p>
</div>
