<script lang="ts">
	// Configuration, laid out as OpenPnP's Machine Setup: the machine as a tree on the left (New … on each group opens a
	// type chooser and creates the object right away), and the selected object's settings on the right in hand-made
	// tabs (lib/config/layouts.ts) with Apply / Reset. "All settings" has every field of the type's schema (also plugin
	// types), "Source" the object as JSON. Changes go through config.update, which validates the whole configuration
	// first and keeps the file's comments.
	import { tinker, RpcError } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import SchemaFields from '#lib/components/config/SchemaFields.svelte';
	import LayoutTab from '#lib/components/config/LayoutTab.svelte';
	import NewObjectDialog from '#lib/components/config/NewObjectDialog.svelte';
	import { segmentItem } from '#lib/styles.js';
	import { layoutFor, type LayoutContext } from '#lib/config/layouts.js';
	import type { Defs, Schema } from '#lib/config/schema.js';
	import {
		Camera,
		Check,
		ChevronDown,
		ChevronRight,
		CircleCheck,
		CircleX,
		Cpu,
		Crosshair,
		Disc,
		Factory,
		History,
		Layers,
		Move3d,
		Plus,
		RotateCcw,
		Search,
		Settings2,
		Trash2,
		TriangleAlert,
		Undo2,
		Zap
	} from '@lucide/svelte';

	type JsonSchema = Schema & { $defs?: Defs };
	type TypeInfo = { kind: string; type: string; title: string; description: string; plugin: string | null; schema: JsonSchema };
	type HistoryItem = { revision: number; time: number; client: string; summary: string };
	type Problem = { path: string; message: string };
	type Obj = Record<string, unknown>;

	// section -> object kind (config.types), in the order of the file
	const SECTIONS: [string, string][] = [
		['drivers', 'driver'],
		['axes', 'axis'],
		['heads', 'head'],
		['cameras', 'camera'],
		['nozzle_tips', 'nozzle_tip'],
		['actuators', 'actuator'],
		['feeders', 'feeder']
	];
	const SINGULAR: Record<string, string> = {
		driver: 'driver', axis: 'axis', head: 'head', nozzle_tip: 'nozzle tip', actuator: 'actuator',
		camera: 'camera', feeder: 'feeder', nozzle: 'nozzle', machine: 'machine'
	};
	const SECTION_LABEL: Record<string, string> = {
		drivers: 'Drivers', axes: 'Axes', heads: 'Heads', cameras: 'Cameras on the machine', nozzle_tips: 'Nozzle tips',
		actuators: 'Actuators', feeders: 'Feeders'
	};
	const ICON = { driver: Cpu, axis: Move3d, head: Crosshair, camera: Camera, nozzle_tip: Disc, actuator: Zap, feeder: Layers, nozzle: Crosshair, machine: Factory } as const;

	let tab = $state<'edit' | 'types' | 'history' | 'file'>('edit');
	let types = $state<TypeInfo[]>([]);
	let config = $state<Obj>({});
	let revision = $state<number | null>(null);
	let history = $state<HistoryItem[]>([]);
	let loadError = $state<string | null>(null);

	// the selected object and its unsaved settings
	let selected = $state<string | null>(null);
	let draft = $state<Obj>({});
	let original = $state('');
	let objTab = $state('');
	let sourceText = $state('');
	let sourceError = $state('');
	let confirmDelete = $state(false);
	let pendingSelect = $state<string | null>(null);
	// the "New …" dialog
	let adding = $state<{ kind: string; base: string } | null>(null);

	let problems = $state<Problem[]>([]);
	let message = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);
	let busy = $state(false);
	let historyMessage = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);

	// tree
	let search = $state('');
	let collapsed = $state<Record<string, boolean>>({});

	const machineEnabled = $derived(tinker.status['machine']?.enabled === true);

	async function load() {
		try {
			const [t, c, h] = await Promise.all([
				tinker.call<TypeInfo[]>('config.types'),
				tinker.call<{ revision: number; config: Obj }>('config.get'),
				tinker.call<HistoryItem[]>('config.history').catch(() => [])
			]);
			types = t;
			config = c.config;
			revision = c.revision;
			history = h;
			loadError = null;
			if (selected && valueAt(selected) === undefined) selected = null; // deleted elsewhere
			else if (selected && !dirty) select(selected, true); // refresh an unmodified editor
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
		return path.split('.').reduce<unknown>((node, key) => (node as Obj | undefined)?.[key], config);
	}
	const idsOf = (section: string) => Object.keys((config[section] as Obj | undefined) ?? {});

	/** The object kind at a config path: cameras.x -> camera, heads.H1.nozzles.N1 -> nozzle, machine -> machine. */
	function kindAt(path: string): string | null {
		const p = path.split('.');
		if (path === 'machine') return 'machine';
		if (p[0] === 'heads' && p.length === 4) return p[2] === 'nozzles' ? 'nozzle' : p[2] === 'cameras' ? 'camera' : null;
		if (p.length === 2) return SECTIONS.find(([s]) => s === p[0])?.[1] ?? null;
		return null;
	}
	const typesOf = (kind: string) => types.filter((t) => t.kind === kind);
	function typeInfo(kind: string | null, type: unknown): TypeInfo | undefined {
		if (!kind) return undefined;
		const list = typesOf(kind);
		return list.find((t) => t.type === type) ?? (type === undefined && list.length === 1 ? list[0] : undefined);
	}

	const dirty = $derived(selected !== null && JSON.stringify(draft) !== original);

	function select(path: string, keepMessage = false) {
		if (dirty && path !== selected && !keepMessage) {
			pendingSelect = path;
			return;
		}
		pendingSelect = null;
		if (path !== selected && objTab.startsWith('_')) objTab = ''; // a new object opens on its first own tab
		selected = path;
		confirmDelete = false;
		const v = (valueAt(path) ?? {}) as Obj;
		draft = structuredClone($state.snapshot(v) as Obj);
		original = JSON.stringify(draft);
		sourceText = JSON.stringify(draft, null, 2);
		sourceError = '';
		if (!keepMessage) {
			problems = [];
			message = null;
		}
	}

	// the selected object
	const selKind = $derived(selected ? kindAt(selected) : null);
	const selType = $derived(selKind ? typeInfo(selKind, draft.type) : undefined);
	const ctx = $derived<LayoutContext>({
		kind: selKind ?? '',
		type: (draft.type as string | undefined) ?? selType?.type ?? '',
		path: selected ?? '',
		value: draft,
		onHead: (selected ?? '').startsWith('heads.')
	});
	const layoutTabs = $derived(selected ? layoutFor(ctx) : []);
	const allTabs = $derived([
		...layoutTabs.map((t) => ({ id: t.id, label: t.label })),
		...(selType ? [{ id: '_all', label: 'All settings' }] : []),
		{ id: '_source', label: 'Source' }
	]);
	$effect(() => {
		// keep the tab when switching between objects that have it
		if (selected && !allTabs.some((t) => t.id === objTab)) objTab = allTabs[0]?.id ?? '_source';
	});
	const savedSelected = $derived(!dirty);

	// existing ids for the fields that name other objects
	const refLists = $derived(Object.fromEntries(SECTIONS.map(([s]) => [s, idsOf(s)])) as Record<string, string[]>);

	function openTab(id: string) {
		if (objTab === '_source' && id !== '_source' && !applySource()) return;
		if (id === '_source') sourceText = JSON.stringify(draft, null, 2);
		objTab = id;
	}
	function applySource(): boolean {
		try {
			draft = JSON.parse(sourceText);
			sourceError = '';
			return true;
		} catch (e) {
			sourceError = `Not valid JSON: ${(e as Error).message}`;
			return false;
		}
	}

	function reset() {
		draft = JSON.parse(original);
		sourceText = JSON.stringify(draft, null, 2);
		sourceError = '';
		problems = [];
		message = null;
	}

	/** Send changes; returns true when applied (or valid, for a dry run). */
	async function send(changes: Obj[], dryRun: boolean, okText: string): Promise<boolean> {
		busy = true;
		problems = [];
		message = null;
		try {
			const r = await tinker.call<{ revision: number; applied: boolean }>('config.update', { revision, dry_run: dryRun, changes });
			message = { kind: 'ok', text: dryRun ? 'Valid. Nothing was saved.' : okText.replace('{rev}', String(r.revision)) };
			return true;
		} catch (e) {
			if (e instanceof RpcError) {
				problems = (e.data.errors as Problem[] | undefined) ?? [];
				message = { kind: 'error', text: e.message };
			} else {
				message = { kind: 'error', text: String(e) };
			}
			return false;
		} finally {
			busy = false;
		}
	}

	async function applyChanges(dryRun: boolean): Promise<boolean> {
		if (!selected) return false;
		if (objTab === '_source' && !applySource()) return false;
		const value = $state.snapshot(draft);
		const ok = await send([{ op: 'set', path: selected, value }], dryRun, 'Applied (revision {rev}).');
		if (ok && !dryRun) original = JSON.stringify(value);
		return ok;
	}

	async function resolvePending(action: 'apply' | 'discard' | 'stay') {
		const next = pendingSelect;
		if (action === 'stay' || !next) return (pendingSelect = null);
		if (action === 'apply' && !(await applyChanges(false))) return (pendingSelect = null);
		if (action === 'discard') reset();
		original = JSON.stringify(draft);
		select(next, action === 'apply');
	}

	async function remove() {
		if (!selected) return;
		const path = selected;
		if (await send([{ op: 'delete', path }], false, `Deleted ${path} (revision {rev}).`)) {
			selected = null;
			confirmDelete = false;
		}
	}

	async function create(path: string, value: Obj) {
		busy = true;
		try {
			const r = await tinker.call<{ revision: number }>('config.update', { revision, changes: [{ op: 'add', path, value }] });
			adding = null;
			await load();
			original = JSON.stringify(draft); // don't ask about the old object's changes
			select(path, true);
			objTab = allTabs[0]?.id ?? '_source';
			message = { kind: 'ok', text: `Created ${path} (revision ${r.revision}). Set it up on its tabs.` };
			return null;
		} catch (e) {
			if (e instanceof RpcError) return { message: e.message, problems: (e.data.errors as Problem[] | undefined) ?? [] };
			return { message: String(e), problems: [] };
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

	// ── tree ─────────────────────────────────────────────────────────────────────────────────────────────────────
	type Node = { path: string; id: string; name?: string; kind: string; type?: string; children?: { label: string; kind: string; base: string; nodes: Node[] }[] };
	function node(path: string, kind: string): Node {
		const v = (valueAt(path) ?? {}) as Obj;
		const id = path.split('.').pop() ?? path;
		return { path, id, kind, name: v.name as string | undefined, type: v.type as string | undefined };
	}
	const tree = $derived(
		SECTIONS.map(([section, kind]) => ({
			section,
			kind,
			nodes: idsOf(section).map((id) => {
				const n = node(`${section}.${id}`, kind);
				if (section === 'heads') {
					const h = (config.heads as Obj)[id] as Obj;
					n.children = (['nozzles', 'cameras'] as const).map((sub) => ({
						label: sub === 'nozzles' ? 'Nozzles' : 'Cameras',
						kind: sub === 'nozzles' ? 'nozzle' : 'camera',
						base: `heads.${id}.${sub}`,
						nodes: Object.keys((h?.[sub] as Obj | undefined) ?? {}).map((sid) => node(`heads.${id}.${sub}.${sid}`, sub === 'nozzles' ? 'nozzle' : 'camera'))
					}));
				}
				return n;
			})
		}))
	);
	const q = $derived(search.trim().toLowerCase());
	const matches = (n: Node) => !q || [n.id, n.name, n.type, n.kind].some((s) => s?.toLowerCase().includes(q));
	const visible = (n: Node): boolean => matches(n) || (n.children ?? []).some((c) => c.nodes.some(matches));

	function startAdd(kind: string, base: string) {
		if (!typesOf(kind).length) return;
		adding = { kind, base };
	}

	const byKind = $derived(
		types.reduce<Record<string, TypeInfo[]>>((acc, t) => {
			(acc[t.kind] ??= []).push(t);
			return acc;
		}, {})
	);

	function typeName(p: Schema): string {
		if (p.type) return Array.isArray(p.type) ? p.type.join(' | ') : p.type;
		if (p.$ref) return p.$ref.split('/').pop() ?? 'object';
		if (p.anyOf) return p.anyOf.map((a) => (a.type as string) ?? a.$ref?.split('/').pop() ?? '?').join(' | ');
		return 'any';
	}

	const tabs = [
		{ id: 'edit', label: 'Machine setup' },
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

{#if adding}
	<NewObjectDialog
		kind={adding.kind}
		base={adding.base}
		types={typesOf(adding.kind)}
		{config}
		refs={refLists}
		label={SINGULAR[adding.kind]}
		oncancel={() => (adding = null)}
		oncreate={create}
	/>
{/if}

{#snippet treeRow(n: Node, depth: number)}
	{@const Icon = ICON[n.kind as keyof typeof ICON] ?? Settings2}
	<button
		class="flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-xs transition-colors {selected === n.path
			? 'bg-primary/15 font-semibold text-primary'
			: 'text-foreground/90 hover:bg-accent'}"
		style="padding-left: {0.5 + depth * 1}rem"
		onclick={() => select(n.path)}
	>
		<Icon class="size-3.5 shrink-0 {selected === n.path ? '' : 'text-muted-foreground'}" />
		<span class="shrink-0 font-mono">{n.id}</span>
		{#if n.name}<span class="truncate text-[11px] font-normal text-muted-foreground">{n.name}</span>{/if}
		{#if n.type}<span class="ml-auto shrink-0 text-[10px] font-normal text-muted-foreground">{n.type}</span>{/if}
	</button>
{/snippet}

{#snippet groupHeader(key: string, label: string, count: number, kind: string, base: string, depth: number)}
	<div class="flex items-center gap-1" style="padding-left: {depth * 1}rem">
		<button class="flex min-w-0 flex-1 items-center gap-1 rounded-md px-1 py-1 text-left hover:bg-accent" onclick={() => (collapsed = { ...collapsed, [key]: !collapsed[key] })}>
			{#if collapsed[key] && !q}<ChevronRight class="size-3.5 text-muted-foreground" />{:else}<ChevronDown class="size-3.5 text-muted-foreground" />{/if}
			<span class="truncate text-xs font-medium">{label}</span>
			<span class="text-[11px] text-muted-foreground">{count}</span>
		</button>
		{#if typesOf(kind).length}
			<Button variant="ghost" size="icon-xs" title="New {SINGULAR[kind]}…" disabled={machineEnabled} onclick={() => startAdd(kind, base)}><Plus /></Button>
		{/if}
	</div>
{/snippet}

{#snippet feedback()}
	{#if message}
		<p class="flex items-center gap-1.5 text-sm {message.kind === 'ok' ? 'text-ok' : 'text-alarm'}">
			{#if message.kind === 'ok'}<CircleCheck class="size-4 shrink-0" />{:else}<CircleX class="size-4 shrink-0" />{/if}{message.text}
		</p>
	{/if}
	{#if problems.length}
		<ul class="rounded-lg border border-alarm/30 bg-alarm/5 p-3 text-xs leading-5">
			{#each problems as p (p.path + p.message)}
				<li><span class="font-mono">{p.path}</span>: {p.message}</li>
			{/each}
		</ul>
	{/if}
{/snippet}

{#if tab === 'edit'}
	{#if machineEnabled}
		<p class="mb-3 flex items-center gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-sm">
			<TriangleAlert class="size-4 shrink-0 text-warn" />
			The machine is enabled: only calibration values (locations, offsets, scales, names) can change now. Disable it to
			add, delete or restructure objects.
		</p>
	{/if}
	<div class="grid items-start gap-4 lg:grid-cols-[18rem_1fr]">
		<!-- the machine as a tree -->
		<Card.Card size="sm" class="gap-2 lg:sticky lg:top-18 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto">
			<Card.CardContent class="flex flex-col gap-0.5 text-sm">
				<div class="relative mb-1.5">
					<Search class="pointer-events-none absolute top-2 left-2 size-3.5 text-muted-foreground" />
					<input class="h-8 w-full rounded-md border border-input bg-background pr-2 pl-7 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50" placeholder="Search" bind:value={search} />
				</div>
				{@render treeRow({ path: 'machine', id: 'Machine', kind: 'machine', name: (config.name as string | undefined) ?? undefined }, 0)}
				{#each tree as g (g.section)}
					{@const shown = g.nodes.filter(visible)}
					{#if !q || shown.length}
						<div class="mt-1">
							{@render groupHeader(g.section, SECTION_LABEL[g.section], g.nodes.length, g.kind, g.section, 0)}
							{#if !collapsed[g.section] || q}
								{#each shown as n (n.path)}
									{@render treeRow(n, 1)}
									{#each n.children ?? [] as c (c.base)}
										{@const sub = c.nodes.filter((x) => matches(x) || matches(n))}
										{#if !q || sub.length}
											{@render groupHeader(c.base, c.label, c.nodes.length, c.kind, c.base, 1)}
											{#if !collapsed[c.base] || q}
												{#each sub as x (x.path)}{@render treeRow(x, 2)}{/each}
											{/if}
										{/if}
									{/each}
								{/each}
							{/if}
						</div>
					{/if}
				{/each}
			</Card.CardContent>
		</Card.Card>

		<!-- the selected object's settings -->
		<Card.Card class="gap-3">
			{#if selected}
				{@const Icon = ICON[(selKind ?? 'machine') as keyof typeof ICON] ?? Settings2}
				<Card.CardHeader>
					<Card.CardTitle class="flex flex-wrap items-center gap-2 text-base">
						<Icon class="size-4 text-muted-foreground" />
						<span class="font-mono">{selected === 'machine' ? 'Machine' : selected.split('.').pop()}</span>
						{#if draft.name && selected !== 'machine'}<span class="font-normal text-muted-foreground">{draft.name}</span>{/if}
						{#if selType}<Badge variant="outline" class="font-normal">{selType.title}</Badge>{/if}
						{#if selType?.plugin}<Badge variant="secondary">plugin {selType.plugin}</Badge>{/if}
					</Card.CardTitle>
					<Card.CardDescription class="font-mono text-[11px]">{selected}</Card.CardDescription>
					{#if selKind && selKind !== 'machine'}
						<Card.CardAction class="flex items-center gap-1.5">
							{#if confirmDelete}
								<span class="text-xs">Delete <span class="font-mono">{selected.split('.').pop()}</span>?</span>
								<Button variant="destructive" size="sm" disabled={busy} onclick={remove}><Trash2 /> Delete</Button>
								<Button variant="ghost" size="sm" onclick={() => (confirmDelete = false)}>Keep</Button>
							{:else}
								<Button variant="ghost" size="sm" class="text-alarm hover:text-alarm" disabled={machineEnabled} onclick={() => (confirmDelete = true)}><Trash2 /> Delete…</Button>
							{/if}
						</Card.CardAction>
					{/if}
				</Card.CardHeader>
				<Card.CardContent class="flex flex-col gap-4">
					{#if pendingSelect}
						<div class="flex flex-wrap items-center gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-sm">
							<TriangleAlert class="size-4 shrink-0 text-warn" />
							<span>Apply the changes to <span class="font-mono">{selected.split('.').pop()}</span> first?</span>
							<span class="ml-auto flex gap-1.5">
								<Button size="sm" disabled={busy} onclick={() => resolvePending('apply')}><Check /> Apply</Button>
								<Button variant="outline" size="sm" onclick={() => resolvePending('discard')}>Discard</Button>
								<Button variant="ghost" size="sm" onclick={() => resolvePending('stay')}>Stay here</Button>
							</span>
						</div>
					{/if}

					<!-- tabs, as OpenPnP's wizard tabs -->
					<div class="flex flex-wrap gap-x-1 border-b">
						{#each allTabs as t (t.id)}
							<button
								class="-mb-px border-b-2 px-3 py-1.5 text-sm transition-colors {objTab === t.id
									? 'border-primary font-medium text-foreground'
									: 'border-transparent text-muted-foreground hover:text-foreground'} {t.id.startsWith('_') ? 'italic' : ''}"
								onclick={() => openTab(t.id)}>{t.label}</button
							>
						{/each}
					</div>

					{#if objTab === '_all' && selType}
						<p class="text-xs text-muted-foreground">Every setting of the type, from its schema. Empty fields use the default shown greyed.</p>
						<SchemaFields schema={selType.schema} defs={selType.schema.$defs ?? {}} bind:value={draft} hide={['type', ...(selKind === 'head' ? ['nozzles', 'cameras'] : [])]} />
					{:else if objTab === '_source'}
						<p class="text-xs text-muted-foreground">The object as JSON. Values may carry units, e.g. <code>"6000 mm/min"</code>.</p>
						<textarea
							class="min-h-96 w-full rounded-lg border border-input bg-well p-3 font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
							spellcheck="false"
							bind:value={sourceText}
							oninput={() => applySource()}
						></textarea>
						{#if sourceError}<p class="text-xs text-alarm">{sourceError}</p>{/if}
					{:else}
						{@const t = layoutTabs.find((x) => x.id === objTab)}
						{#if t}
							<LayoutTab groups={t.groups} {ctx} schema={selType?.schema} defs={selType?.schema.$defs ?? {}} bind:value={draft} {config} refs={refLists} saved={savedSelected} />
						{/if}
					{/if}

					<div class="sticky bottom-0 -mx-4 flex flex-wrap items-center gap-2 border-t bg-card px-4 pt-3 pb-1">
						<Button disabled={busy || !dirty} onclick={() => applyChanges(false)}><Check /> Apply</Button>
						<Button variant="outline" disabled={busy || !dirty} onclick={reset}><RotateCcw /> Reset</Button>
						<Button variant="ghost" disabled={busy} onclick={() => applyChanges(true)} title="Check the whole configuration with these changes, without saving">Validate</Button>
						{#if dirty}<Badge variant="warn">unsaved changes</Badge>{/if}
					</div>
					{@render feedback()}
				</Card.CardContent>
			{:else}
				<Card.CardContent class="py-10 text-center text-sm text-muted-foreground">
					Pick something on the left to set it up, or <Plus class="inline size-3.5" /> on a group to add one.
				</Card.CardContent>
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
