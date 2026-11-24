<script lang="ts">
	import { tinker, RpcError } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { segmentItem } from '#lib/styles.js';
	import { Check, CircleCheck, CircleX, FileCode, History, RotateCcw, Save, Undo2 } from '@lucide/svelte';

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

<div class="mb-4 flex flex-wrap items-center gap-3">
	<ToggleGroup.Root type="single" variant="outline" size="sm" value={tab} onValueChange={(v) => v && (tab = v as typeof tab)}>
		{#each tabs as t (t.id)}
			<ToggleGroup.Item value={t.id} class={segmentItem}>{t.label}</ToggleGroup.Item>
		{/each}
	</ToggleGroup.Root>
	{#if revision !== null}<Badge variant="outline" class="ml-auto font-mono">revision {revision}</Badge>{/if}
</div>

{#if loadError}<p class="mb-2 text-sm text-alarm">{loadError}</p>{/if}

{#if tab === 'edit'}
	<div class="grid items-start gap-4 lg:grid-cols-[15rem_1fr]">
		<Card.Card size="sm" class="gap-0">
			<Card.CardContent class="text-sm">
				{#each SECTIONS as section (section)}
					{@const items = objectsIn(section)}
					{#if items.length}
						<div class="eyebrow mt-3 mb-1 px-2 first:mt-0">{section.replace('_', ' ')}</div>
						{#each items as path (path)}
							<button
								class="block w-full rounded-md px-2 py-1 text-left font-mono text-xs transition-colors hover:bg-accent {selected === path ? 'bg-primary/15 font-semibold text-primary' : 'text-foreground/90'}"
								onclick={() => select(path)}
							>
								{path === 'machine' ? 'machine' : path.split('.').slice(1).join('.')}
							</button>
						{/each}
					{/if}
				{/each}
			</Card.CardContent>
		</Card.Card>

		<Card.Card class="gap-3">
			{#if selected}
				<Card.CardHeader>
					<Card.CardTitle class="flex items-center gap-2 font-mono text-sm"><FileCode class="size-4 text-muted-foreground" />{selected}</Card.CardTitle>
					<Card.CardDescription>
						Settings as JSON. Values may carry units, e.g. <code>"6000 mm/min"</code>. Validate checks the whole
						configuration without saving; Save writes the file (comments are kept) and rebuilds the machine.
					</Card.CardDescription>
				</Card.CardHeader>
				<Card.CardContent class="flex flex-col gap-3">
					<textarea
						class="min-h-96 w-full rounded-lg border border-input bg-well p-3 font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
						spellcheck="false"
						bind:value={text}
					></textarea>
					<div class="flex items-center gap-2">
						<Button variant="outline" disabled={busy} onclick={() => submit(true)}><Check /> Validate</Button>
						<Button disabled={busy || text === original} onclick={() => submit(false)}><Save /> Save</Button>
						<Button variant="ghost" disabled={busy || text === original} onclick={() => (text = original)}><RotateCcw /> Revert</Button>
						{#if text !== original}<Badge variant="warn" class="ml-auto">unsaved changes</Badge>{/if}
					</div>
					{#if message}
						<p class="flex items-center gap-1.5 text-sm {message.kind === 'ok' ? 'text-ok' : 'text-alarm'}">
							{#if message.kind === 'ok'}<CircleCheck class="size-4" />{:else}<CircleX class="size-4" />{/if}{message.text}
						</p>
					{/if}
					{#if problems.length}
						<ul class="rounded-lg border border-alarm/30 bg-alarm/5 p-3 text-xs leading-5">
							{#each problems as p (p.path + p.message)}
								<li><span class="font-mono">{p.path}</span>: {p.message}</li>
							{/each}
						</ul>
					{/if}
				</Card.CardContent>
			{:else}
				<Card.CardContent class="py-10 text-center text-sm text-muted-foreground">Pick an object on the left to edit it.</Card.CardContent>
			{/if}
		</Card.Card>
	</div>
{:else if tab === 'types'}
	<p class="mb-4 text-sm text-muted-foreground">
		Every object type the server knows, including plugin types, with the settings fields from its JSON Schema.
	</p>
	{#each Object.entries(byKind) as [kind, list] (kind)}
		<h2 class="mt-6 mb-2 text-base font-semibold capitalize first:mt-0">{kind.replace('_', ' ')}</h2>
		<div class="grid gap-3 lg:grid-cols-2">
			{#each list as t (t.type)}
				<Card.Card size="sm" class="gap-2">
					<Card.CardHeader>
						<Card.CardTitle class="flex items-center gap-2 text-sm">
							{t.title}
							<Badge variant="outline" class="font-mono">{t.type}</Badge>
							{#if t.plugin}<Badge variant="secondary">plugin: {t.plugin}</Badge>{/if}
						</Card.CardTitle>
						{#if t.description}<Card.CardDescription>{t.description}</Card.CardDescription>{/if}
					</Card.CardHeader>
					<Card.CardContent>
						<Table.Table>
							<Table.TableBody>
								{#each Object.entries(t.schema.properties ?? {}) as [field, p] (field)}
									<Table.TableRow>
										<Table.TableCell class="font-mono text-xs">
											{field}{#if t.schema.required?.includes(field)}<span class="text-alarm"> *</span>{/if}
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
	<Card.Card class="gap-3">
		<Card.CardHeader><Card.CardTitle class="flex items-center gap-2 text-sm"><History class="size-4 text-muted-foreground" /> Changes since the server started</Card.CardTitle></Card.CardHeader>
		<Card.CardContent>
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
								<Table.TableCell class="num">{h.revision}</Table.TableCell>
								<Table.TableCell class="text-xs">{new Date(h.time * 1000).toLocaleString()}</Table.TableCell>
								<Table.TableCell class="text-xs">{h.client}</Table.TableCell>
								<Table.TableCell class="font-mono text-xs">{h.summary}</Table.TableCell>
								<Table.TableCell class="text-right">
									<Button variant="outline" size="sm" disabled={busy} onclick={() => undoTo(h.revision - 1)}>
										<Undo2 /> Back to {h.revision - 1}
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
				<p class="mt-2 text-sm {historyMessage.kind === 'ok' ? 'text-ok' : 'text-alarm'}">{historyMessage.text}</p>
			{/if}
		</Card.CardContent>
	</Card.Card>
{:else}
	<pre class="overflow-x-auto rounded-xl border bg-well p-4 font-mono text-xs leading-5">{JSON.stringify(config, null, 2)}</pre>
{/if}
