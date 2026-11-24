<script lang="ts">
	// Digital readout: the selected tool's position, one cell per axis, colour-coded like on CNC machines.
	let { location }: { location: { x?: number; y?: number; z?: number; rotation?: number } } = $props();

	const cells = $derived([
		{ axis: 'X', value: location.x, unit: 'mm', bar: 'bg-axis-x', text: 'text-axis-x' },
		{ axis: 'Y', value: location.y, unit: 'mm', bar: 'bg-axis-y', text: 'text-axis-y' },
		{ axis: 'Z', value: location.z, unit: 'mm', bar: 'bg-axis-z', text: 'text-axis-z' },
		{ axis: 'R', value: location.rotation, unit: '°', bar: 'bg-axis-r', text: 'text-axis-r' }
	]);
</script>

<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
	{#each cells as c (c.axis)}
		<div class="relative overflow-hidden rounded-lg bg-well px-3 py-2">
			<span class="absolute inset-y-0 left-0 w-1 {c.bar}"></span>
			<div class="flex items-baseline justify-between">
				<span class="text-xs font-bold {c.text}">{c.axis}</span>
				<span class="text-[10px] text-muted-foreground">{c.unit}</span>
			</div>
			<div class="num text-right text-xl leading-tight font-semibold tracking-tight">
				{typeof c.value === 'number' ? c.value.toFixed(c.axis === 'R' ? 2 : 3) : '—'}
			</div>
		</div>
	{/each}
</div>
