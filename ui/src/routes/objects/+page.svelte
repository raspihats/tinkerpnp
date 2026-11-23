<script lang="ts">
	import { tinker, kindOf } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';

	let filter = $state('');
	let kind = $state('all');

	const kinds = $derived(['all', ...new Set(tinker.objects.map(kindOf))]);
	const shown = $derived(
		tinker.objects.filter(
			(n) => (kind === 'all' || kindOf(n) === kind) && n.toLowerCase().includes(filter.toLowerCase())
		)
	);
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
	<Input class="max-w-xs" placeholder="Filter objects…" bind:value={filter} />
	{#each kinds as k (k)}
		<button onclick={() => (kind = k)}>
			<Badge variant={kind === k ? 'default' : 'outline'}>{k}</Badge>
		</button>
	{/each}
	<span class="ml-auto text-sm text-muted-foreground">{shown.length} of {tinker.objects.length} objects, live</span>
</div>

<div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
	{#each shown as name (name)}
		<Card.Card class="gap-2 py-3">
			<Card.CardHeader class="px-3">
				<Card.CardTitle class="font-mono text-sm">{name}</Card.CardTitle>
			</Card.CardHeader>
			<Card.CardContent class="px-3">
				<pre class="overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(tinker.status[name] ?? {}, null, 2)}</pre>
			</Card.CardContent>
		</Card.Card>
	{/each}
</div>
