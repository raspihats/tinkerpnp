<script lang="ts">
	import { tinker } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';

	type JsonSchema = {
		properties?: Record<string, { type?: string; description?: string; default?: unknown; anyOf?: { type?: string; $ref?: string }[]; $ref?: string }>;
		required?: string[];
	};
	type TypeInfo = { kind: string; type: string; title: string; description: string; plugin: string | null; schema: JsonSchema };

	let types = $state<TypeInfo[]>([]);
	let config = $state<unknown>(null);
	let revision = $state<number | null>(null);
	let error = $state<string | null>(null);
	let tab = $state<'types' | 'file'>('types');

	$effect(() => {
		if (tinker.connection !== 'connected') return;
		Promise.all([tinker.call<TypeInfo[]>('config.types'), tinker.call<{ revision: number; config: unknown }>('config.get')])
			.then(([t, c]) => {
				types = t;
				config = c.config;
				revision = c.revision;
			})
			.catch((e) => (error = String(e)));
	});

	const byKind = $derived(
		types.reduce<Record<string, TypeInfo[]>>((acc, t) => {
			(acc[t.kind] ??= []).push(t);
			return acc;
		}, {})
	);

	function typeName(p: NonNullable<JsonSchema['properties']>[string]): string {
		if (p.type) return p.type;
		if (p.$ref) return p.$ref.split('/').pop() ?? 'object';
		if (p.anyOf) return p.anyOf.map((a) => a.type ?? a.$ref?.split('/').pop() ?? '?').join(' | ');
		return 'any';
	}
</script>

<div class="mb-4 flex items-center gap-2">
	<button onclick={() => (tab = 'types')}><Badge variant={tab === 'types' ? 'default' : 'outline'}>Object types</Badge></button>
	<button onclick={() => (tab = 'file')}><Badge variant={tab === 'file' ? 'default' : 'outline'}>Machine configuration</Badge></button>
	{#if revision !== null}<span class="ml-auto text-sm text-muted-foreground">revision {revision}</span>{/if}
</div>

{#if error}<p class="text-destructive">{error}</p>{/if}

{#if tab === 'types'}
	<p class="mb-4 text-sm text-muted-foreground">
		Every object type the server knows, including plugin types, with the settings fields from its JSON Schema.
		Editing comes with <code>config.update</code>.
	</p>
	{#each Object.entries(byKind) as [kind, list] (kind)}
		<h2 class="mt-6 mb-2 text-lg font-semibold capitalize">{kind.replace('_', ' ')}</h2>
		<div class="grid gap-3 lg:grid-cols-2">
			{#each list as t (t.type)}
				<Card.Card class="gap-2 py-3">
					<Card.CardHeader class="px-3">
						<Card.CardTitle class="flex items-center gap-2 text-sm">
							{t.title}
							<Badge variant="outline" class="font-mono">{t.type}</Badge>
							{#if t.plugin}<Badge variant="secondary">plugin: {t.plugin}</Badge>{/if}
						</Card.CardTitle>
						{#if t.description}<Card.CardDescription>{t.description}</Card.CardDescription>{/if}
					</Card.CardHeader>
					<Card.CardContent class="px-3">
						<Table.Table>
							<Table.TableBody>
								{#each Object.entries(t.schema.properties ?? {}) as [field, p] (field)}
									<Table.TableRow>
										<Table.TableCell class="font-mono text-xs">
											{field}{#if t.schema.required?.includes(field)}<span class="text-destructive"> *</span>{/if}
										</Table.TableCell>
										<Table.TableCell class="text-xs text-muted-foreground">{typeName(p)}</Table.TableCell>
										<Table.TableCell class="text-xs">{p.description ?? ''}</Table.TableCell>
									</Table.TableRow>
								{/each}
							</Table.TableBody>
						</Table.Table>
					</Card.CardContent>
				</Card.Card>
			{/each}
		</div>
	{/each}
{:else}
	<pre class="overflow-x-auto rounded-lg bg-muted p-3 text-xs">{JSON.stringify(config, null, 2)}</pre>
{/if}
