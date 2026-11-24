<script lang="ts">
	import { tinker, kindOf, idOf, fmt } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import { stateVariant } from '#lib/status.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Search } from '@lucide/svelte';

	let filter = $state('');
	let kind = $state('all');

	const kinds = $derived(['all', ...new Set(tinker.objects.map(kindOf))]);
	const shown = $derived(
		tinker.objects.filter((n) => (kind === 'all' || kindOf(n) === kind) && n.toLowerCase().includes(filter.toLowerCase()))
	);

	// one status value as text; objects (locations) as "x 1 · y 2"
	function show(v: unknown): string {
		if (v && typeof v === 'object' && !Array.isArray(v)) {
			return Object.entries(v as Record<string, unknown>)
				.map(([k, x]) => `${k} ${show(x)}`)
				.join(' · ');
		}
		if (Array.isArray(v)) return v.map((x) => show(x)).join(', ') || '–';
		return fmt(v);
	}
</script>

<div class="mb-4 flex flex-wrap items-center gap-3">
	<div class="relative w-full max-w-xs">
		<Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
		<Input class="pl-8" placeholder="Filter objects…" bind:value={filter} />
	</div>
	<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={kind} class="flex-wrap">
		{#each kinds as k (k)}
			<ToggleGroup.Item value={k} class="capitalize {segmentItem}">{k.replace('_', ' ')}</ToggleGroup.Item>
		{/each}
	</ToggleGroup.Root>
	<span class="ml-auto text-xs text-muted-foreground">{shown.length} of {tinker.objects.length} objects · live</span>
</div>

<div class="grid items-start gap-3 md:grid-cols-2 xl:grid-cols-3">
	{#each shown as name (name)}
		{@const s = tinker.status[name] ?? {}}
		<Card.Root size="sm" class="gap-2">
			<Card.Header class="flex items-center gap-2">
				<Badge variant="outline" class="capitalize">{kindOf(name).replace('_', ' ')}</Badge>
				<Card.Title class="font-mono text-sm">{name.includes('/') ? idOf(name) : name}</Card.Title>
				{#if s.state}<Card.Action><Badge variant={stateVariant(s.state)} class="capitalize">{s.state}</Badge></Card.Action>{/if}
			</Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
					{#each Object.entries(s).filter(([k]) => k !== 'state') as [k, v] (k)}
						<dt class="text-muted-foreground">{k.replaceAll('_', ' ')}</dt>
						<dd class="num truncate" title={show(v)}>{show(v)}</dd>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>
	{/each}
</div>
