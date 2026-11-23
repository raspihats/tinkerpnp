<script lang="ts">
	import { tinker, RpcError } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';

	type JsonSchema = {
		properties?: Record<string, { type?: string; description?: string; default?: unknown; anyOf?: { type?: string; $ref?: string }[]; $ref?: string }>;
		required?: string[];
	};
	type TypeInfo = { kind: string; type: string; title: string; description: string; plugin: string | null; schema: JsonSchema };
	type HistoryItem = { revision: number; time: number; client: string; summary: string };
	type Problem = { path: string; message: string };

	const SECTIONS = ['machine', 'drivers', 'axes', 'heads', 'nozzle_tips', 'actuators', 'cameras', 'feeders'];

	let tab = $state<'edit' | 'types' | 'history' | 'file'>('edit');
	let types = $state<TypeInfo[]>([]);
	let config = $state<Record<string, unknown>>({});
	let revision = $state<number | null>(null);
	let history = $state<HistoryItem[]>([]);
	let loadError = $state<string | null>(null);

	// editor
	let selected = $state<string | null>(null);
	let text = $state('');
	let original = $state('');
	let problems = $state<Problem[]>([]);
	let message = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);
	let busy = $state(false);
	let historyMessage = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);

	async function load() {
		try {
			const [t, c, h] = await Promise.all([
				tinker.call<TypeInfo[]>('config.types'),
				tinker.call<{ revision: number; config: Record<string, unknown> }>('config.get'),
				tinker.call<HistoryItem[]>('config.history').catch(() => [])
			]);
			types = t;
			config = c.config;
			revision = c.revision;
			history = h;
			loadError = null;
			if (selected && text === original) select(selected, true); // refresh an unmodified editor
		} catch (e) {
			loadError = String(e);
		}
	}

	$effect(() => {
		// reload when connected and whenever any client changed the configuration
		void tinker.configRevision;
		if (tinker.connection === 'connected') load();
	});

	function valueAt(path: string): unknown {
		return path.split('.').reduce<unknown>((node, key) => (node as Record<string, unknown> | undefined)?.[key], config);
	}

	function select(path: string, keepMessage = false) {
		selected = path;
		original = JSON.stringify(valueAt(path) ?? {}, null, 2);
		text = original;
		if (!keepMessage) {
			problems = [];
			message = null;
		}
	}

	function objectsIn(section: string): string[] {
		if (section === 'machine') return ['machine'];
		const content = config[section];
		return content && typeof content === 'object' ? Object.keys(content).map((id) => `${section}.${id}`) : [];
	}

	async function submit(dryRun: boolean) {
		if (!selected) return;
		let value: unknown;
		try {
			value = JSON.parse(text);
		} catch (e) {
			message = { kind: 'error', text: `Not valid JSON: ${(e as Error).message}` };
			return;
		}
		busy = true;
		problems = [];
		message = null;
		try {
			const r = await tinker.call<{ revision: number; applied: boolean }>('config.update', {
				revision,
				dry_run: dryRun,
				changes: [{ op: 'set', path: selected, value }]
			});
			if (dryRun) {
				message = { kind: 'ok', text: 'Valid. Nothing was saved.' };
			} else {
				message = { kind: 'ok', text: `Saved as revision ${r.revision}. The machine was rebuilt from it.` };
				original = text;
			}
		} catch (e) {
			if (e instanceof RpcError) {
				problems = (e.data.errors as Problem[] | undefined) ?? [];
				message = { kind: 'error', text: e.message };
			} else {
				message = { kind: 'error', text: String(e) };
			}
		} finally {
			busy = false;
		}
	}

	async function undoTo(rev: number) {
		busy = true;
		try {
			const r = await tinker.call<{ revision: number }>('config.undo', { revision: rev });
			historyMessage = { kind: 'ok', text: `Back to the configuration of revision ${rev} (now revision ${r.revision}).` };
		} catch (e) {
			historyMessage = { kind: 'error', text: e instanceof Error ? e.message : String(e) };
		} finally {
			busy = false;
		}
	}

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

	const tabs = [
		{ id: 'edit', label: 'Edit' },
		{ id: 'types', label: 'Object types' },
		{ id: 'history', label: 'History' },
		{ id: 'file', label: 'Whole configuration' }
	] as const;
</script>

<div class="mb-4 flex items-center gap-2">
	{#each tabs as t (t.id)}
		<button onclick={() => (tab = t.id)}><Badge variant={tab === t.id ? 'default' : 'outline'}>{t.label}</Badge></button>
	{/each}
	{#if revision !== null}<span class="ml-auto text-sm text-muted-foreground">revision {revision}</span>{/if}
</div>

{#if loadError}<p class="mb-2 text-destructive">{loadError}</p>{/if}

{#if tab === 'edit'}
	<div class="grid gap-4 lg:grid-cols-[16rem_1fr]">
		<Card.Card class="gap-2 py-3">
			<Card.CardContent class="px-3 text-sm">
				{#each SECTIONS as section (section)}
					{@const items = objectsIn(section)}
					{#if items.length}
						<div class="mt-2 mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{section.replace('_', ' ')}</div>
						{#each items as path (path)}
							<button
								class="block w-full rounded px-2 py-1 text-left font-mono text-xs hover:bg-muted {selected === path ? 'bg-muted font-semibold' : ''}"
								onclick={() => select(path)}
							>
								{path === 'machine' ? 'machine' : path.split('.').slice(1).join('.')}
							</button>
						{/each}
					{/if}
				{/each}
			</Card.CardContent>
		</Card.Card>

		<Card.Card class="gap-3 py-3">
			{#if selected}
				<Card.CardHeader class="px-3">
					<Card.CardTitle class="font-mono text-sm">{selected}</Card.CardTitle>
					<Card.CardDescription>
						Settings as JSON. Values may carry units, e.g. <code>"6000 mm/min"</code>. Validate checks the whole
						configuration without saving; Save writes the file (comments are kept) and rebuilds the machine.
					</Card.CardDescription>
				</Card.CardHeader>
				<Card.CardContent class="flex flex-col gap-3 px-3">
					<textarea
						class="min-h-80 w-full rounded-md border bg-background p-2 font-mono text-xs"
						spellcheck="false"
						bind:value={text}
					></textarea>
					<div class="flex items-center gap-2">
						<Button variant="outline" size="sm" disabled={busy} onclick={() => submit(true)}>Validate</Button>
						<Button size="sm" disabled={busy || text === original} onclick={() => submit(false)}>Save</Button>
						<Button variant="ghost" size="sm" disabled={busy || text === original} onclick={() => (text = original)}>Revert</Button>
						{#if text !== original}<span class="text-xs text-muted-foreground">unsaved changes</span>{/if}
					</div>
					{#if message}
						<p class="text-sm {message.kind === 'ok' ? 'text-green-700 dark:text-green-400' : 'text-destructive'}">{message.text}</p>
					{/if}
					{#if problems.length}
						<ul class="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs">
							{#each problems as p (p.path + p.message)}
								<li><span class="font-mono">{p.path}</span>: {p.message}</li>
							{/each}
						</ul>
					{/if}
				</Card.CardContent>
			{:else}
				<Card.CardContent class="px-3 text-sm text-muted-foreground">Pick an object on the left to edit it.</Card.CardContent>
			{/if}
		</Card.Card>
	</div>
{:else if tab === 'types'}
	<p class="mb-4 text-sm text-muted-foreground">
		Every object type the server knows, including plugin types, with the settings fields from its JSON Schema.
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
{:else if tab === 'history'}
	<Card.Card class="py-3">
		<Card.CardContent class="px-3">
			{#if history.length}
				<Table.Table>
					<Table.TableHeader>
						<Table.TableRow>
							<Table.TableHead>Revision</Table.TableHead>
							<Table.TableHead>When</Table.TableHead>
							<Table.TableHead>Client</Table.TableHead>
							<Table.TableHead>Change</Table.TableHead>
							<Table.TableHead></Table.TableHead>
						</Table.TableRow>
					</Table.TableHeader>
					<Table.TableBody>
						{#each history as h (h.revision)}
							<Table.TableRow>
								<Table.TableCell>{h.revision}</Table.TableCell>
								<Table.TableCell class="text-xs">{new Date(h.time * 1000).toLocaleString()}</Table.TableCell>
								<Table.TableCell class="text-xs">{h.client}</Table.TableCell>
								<Table.TableCell class="font-mono text-xs">{h.summary}</Table.TableCell>
								<Table.TableCell class="text-right">
									<Button variant="outline" size="sm" disabled={busy} onclick={() => undoTo(h.revision - 1)}>
										Undo (back to {h.revision - 1})
									</Button>
								</Table.TableCell>
							</Table.TableRow>
						{/each}
					</Table.TableBody>
				</Table.Table>
			{:else}
				<p class="text-sm text-muted-foreground">No changes since the server started.</p>
			{/if}
			{#if historyMessage}
				<p class="mt-2 text-sm {historyMessage.kind === 'ok' ? 'text-green-700 dark:text-green-400' : 'text-destructive'}">{historyMessage.text}</p>
			{/if}
		</Card.CardContent>
	</Card.Card>
{:else}
	<pre class="overflow-x-auto rounded-lg bg-muted p-3 text-xs">{JSON.stringify(config, null, 2)}</pre>
{/if}
