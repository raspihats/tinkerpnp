<script lang="ts">
	// Digital readout: the selected tool's position, one cell per axis, colour-coded like on CNC machines.
	// `requested` (the target of the last move of this tool) is shown, small and amber, only when the axis isn't there.
	// With `onset`, a cell can be clicked to type a position for that axis: Enter moves, Esc cancels (OpenPnP's
	// readout is display-only; this saves a separate row of inputs).
	type Key = 'x' | 'y' | 'z' | 'rotation';
	type Loc = { x?: number; y?: number; z?: number; rotation?: number };
	let {
		location,
		requested = null,
		onset = null
	}: { location: Loc; requested?: Loc | null; onset?: ((key: Key, value: number) => void) | null } = $props();

	const cells = $derived(
		(
			[
				['X', 'x', 'mm', 'bg-axis-x', 'text-axis-x', 0.01],
				['Y', 'y', 'mm', 'bg-axis-y', 'text-axis-y', 0.01],
				['Z', 'z', 'mm', 'bg-axis-z', 'text-axis-z', 0.3],
				['R', 'rotation', '°', 'bg-axis-r', 'text-axis-r', 0.05]
			] as const
		).map(([axis, key, unit, bar, text, tol]) => {
			const value = location[key];
			const target = requested?.[key];
			let off = false;
			if (typeof value === 'number' && typeof target === 'number') {
				let d = Math.abs(value - target);
				if (key === 'rotation') d = Math.min(d % 360, 360 - (d % 360)); // 270 and -90 are the same angle
				off = d > tol;
			}
			return { axis, key, value, target, unit, bar, text, off, digits: key === 'rotation' ? 2 : 3 };
		})
	);

	let editing = $state<Key | null>(null);
	let draft = $state('');
	let invalid = $state(false);

	function start(key: Key, value: number | undefined) {
		if (!onset) return;
		editing = key;
		draft = typeof value === 'number' ? String(Number(value.toFixed(key === 'rotation' ? 2 : 3))) : '';
		invalid = false;
	}
	function commit() {
		if (!editing || !onset) return;
		const n = Number(draft.trim().replace(',', '.')); // a decimal comma works too
		if (draft.trim() === '' || !Number.isFinite(n)) {
			invalid = true;
			return;
		}
		onset(editing, n);
		editing = null;
	}
	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			commit();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation(); // cancels the entry: must not reach the page's Esc = STOP
			editing = null;
		}
	}
	function focusSelect(el: HTMLInputElement) {
		el.focus();
		el.select();
	}
</script>

<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
	{#each cells as c (c.axis)}
		<!-- a button while it can be clicked to type a position (onset given) -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			class="group relative overflow-hidden rounded-lg bg-well px-3 py-2 {c.off ? 'ring-1 ring-warn/60' : ''} {editing === c.key
				? 'ring-2 ring-ring/60'
				: ''} {onset && editing !== c.key ? 'cursor-text hover:ring-1 hover:ring-border' : ''}"
			role={onset ? 'button' : undefined}
			tabindex={onset && editing !== c.key ? 0 : undefined}
			title={onset && editing !== c.key ? `Click to enter a ${c.axis} position (${c.unit}); Enter moves, Esc cancels` : undefined}
			onclick={() => editing !== c.key && start(c.key, c.value)}
			onkeydown={(e) => editing !== c.key && e.key === 'Enter' && start(c.key, c.value)}
		>
			<span class="absolute inset-y-0 left-0 w-1 {c.bar}"></span>
			<div class="flex items-baseline justify-between gap-1">
				<span class="text-xs font-bold whitespace-nowrap {c.text}">{c.axis} <span class="font-normal text-muted-foreground">{c.unit}</span></span>
				{#if editing === c.key}
					<span class="text-[10px] whitespace-nowrap {invalid ? 'text-alarm' : 'text-muted-foreground'}">{invalid ? 'not a number' : '↵ go'}</span>
				{:else if c.off && typeof c.target === 'number'}
					<span class="num truncate text-[10px] text-warn" title="sent to {c.target.toFixed(c.digits)}, not there">→ {c.target.toFixed(c.digits)}</span>
				{/if}
			</div>
			{#if editing === c.key}
				<input
					use:focusSelect
					class="num w-full bg-transparent text-right text-xl leading-tight font-semibold tracking-tight outline-none"
					inputmode="decimal"
					aria-label="{c.axis} position ({c.unit})"
					bind:value={draft}
					oninput={() => (invalid = false)}
					onkeydown={onKeydown}
					onblur={() => (editing = null)}
				/>
			{:else}
				<div class="num text-right text-xl leading-tight font-semibold tracking-tight">
					{typeof c.value === 'number' ? c.value.toFixed(c.digits) : '—'}
				</div>
			{/if}
		</div>
	{/each}
</div>
