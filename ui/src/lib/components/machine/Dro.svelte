<script lang="ts">
	// Digital readout: the selected tool's position, one cell per axis, colour-coded like on CNC machines.
	// `requested` (the target of the last move of this tool) is shown small above; amber when the axis isn't there.
	type Loc = { x?: number; y?: number; z?: number; rotation?: number };
	let { location, requested = null }: { location: Loc; requested?: Loc | null } = $props();

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
			return { axis, value, target, unit, bar, text, off, digits: key === 'rotation' ? 2 : 3 };
		})
	);
</script>

<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
	{#each cells as c (c.axis)}
		<div class="relative overflow-hidden rounded-lg bg-well px-3 py-2 {c.off ? 'ring-1 ring-warn/60' : ''}">
			<span class="absolute inset-y-0 left-0 w-1 {c.bar}"></span>
			<div class="flex items-baseline justify-between gap-1">
				<span class="text-xs font-bold {c.text}">{c.axis}</span>
				<span class="num truncate text-[10px] {c.off ? 'text-warn' : 'text-muted-foreground'}" title="target of the last move">
					{typeof c.target === 'number' ? `→ ${c.target.toFixed(c.digits)}` : c.unit}
				</span>
			</div>
			<div class="num text-right text-xl leading-tight font-semibold tracking-tight">
				{typeof c.value === 'number' ? c.value.toFixed(c.digits) : '—'}
			</div>
		</div>
	{/each}
</div>
